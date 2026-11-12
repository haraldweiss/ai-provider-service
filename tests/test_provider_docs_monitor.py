"""Behavioral tests for host-side documentation monitoring and mail delivery."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'check_provider_docs.py'
spec = importlib.util.spec_from_file_location('docs_monitor', SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


def report(rules=None, error=None):
    return {'sources': {'test': {'rules': rules or [], 'error': error}}}


def check(path, current, messages):
    return monitor.run_check(path, lambda: current, messages.append,
                             source_ids={'test'})


def test_first_healthy_run_and_unchanged_runs_do_not_mail(tmp_path):
    path = tmp_path / 'state.json'
    messages = []
    assert check(path, report(['Send Bearer auth.']), messages) == 0
    assert check(path, report(['Send Bearer auth.']), messages) == 0
    assert messages == []
    assert json.loads(path.read_text())['sources']['test']['rules'] == ['Send Bearer auth.']
    assert path.stat().st_mode & 0o777 == 0o600


def test_rule_change_mails_added_and_removed_prose(tmp_path):
    path = tmp_path / 'state.json'
    messages = []
    check(path, report(['Send old-header.']), messages)
    assert check(path, report(['Send new-header.']), messages) == 0
    assert len(messages) == 1
    assert '+ Send new-header.' in messages[0]
    assert '- Send old-header.' in messages[0]
    check(path, report(['Send new-header.']), messages)
    assert len(messages) == 1


def test_fetch_failure_preserves_last_rules_and_alerts_once_then_recovers(tmp_path):
    path = tmp_path / 'state.json'
    messages = []
    check(path, report(['Send auth.']), messages)
    assert check(path, report(error='HTTP 404'), messages) == 1
    assert json.loads(path.read_text())['sources']['test']['rules'] == ['Send auth.']
    assert len(messages) == 1
    assert check(path, report(error='HTTP 404'), messages) == 1
    assert len(messages) == 1
    assert check(path, report(['Send auth.']), messages) == 0
    assert len(messages) == 2
    assert 'Recovered' in messages[-1]


def test_first_run_failures_alert_instead_of_seeding_silently(tmp_path):
    messages = []
    assert check(tmp_path / 'state.json', report(error='empty rules'), messages) == 1
    assert len(messages) == 1


def test_failed_mail_submission_keeps_old_snapshot_for_retry(tmp_path):
    path = tmp_path / 'state.json'
    check(path, report(['Original auth.']), [])
    original = path.read_bytes()

    def failing_mail(body):
        raise subprocess.CalledProcessError(1, 'sendmail')

    assert monitor.run_check(path, lambda: report(['Changed auth.']), failing_mail,
                             source_ids={'test'}) == 1
    assert path.read_bytes() == original
    messages = []
    assert check(path, report(['Changed auth.']), messages) == 0
    assert len(messages) == 1


@pytest.mark.parametrize('bad', [
    {}, {'sources': {}}, report(), {'sources': {'test': {'rules': 'Bearer', 'error': None}}},
    {'sources': {'unexpected': {'rules': ['auth'], 'error': None}}},
])
def test_invalid_collector_data_does_not_replace_good_rules(tmp_path, bad):
    path = tmp_path / 'state.json'
    check(path, report(['Known auth.']), [])
    messages = []
    assert check(path, bad, messages) == 1
    assert json.loads(path.read_text())['sources']['test']['rules'] == ['Known auth.']
    assert len(messages) == 1


def test_collector_failure_and_recovery_alert_once_without_exposing_output(tmp_path):
    path = tmp_path / 'state.json'
    check(path, report(['Auth.']), [])
    messages = []

    def broken():
        raise subprocess.CalledProcessError(1, 'docker', output='opaque-secret-value')

    for _ in range(2):
        assert monitor.run_check(path, broken, messages.append, source_ids={'test'}) == 1
    assert len(messages) == 1
    assert 'opaque-secret-value' not in messages[0]
    assert check(path, report(['Auth.']), messages) == 0
    assert len(messages) == 2
    assert 'Recovered' in messages[-1]


@pytest.mark.parametrize('entry', [
    None, {'rules': None, 'error': None}, {'rules': [None], 'error': None},
    {'rules': ['Auth.'], 'error': 123}, {'rules': ['Auth.']},
])
def test_malformed_saved_source_alerts_and_preserves_file(tmp_path, entry):
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'sources': {'test': entry}}))
    original = path.read_bytes()
    messages = []
    assert check(path, report(['Auth.']), messages) == 1
    assert len(messages) == 1
    assert 'snapshot unreadable' in messages[0]
    assert path.read_bytes() == original


def test_collection_rejects_login_pages_and_empty_extraction():
    source = {'id': 'test', 'url': 'https://docs.example.test/api',
              'format': 'markdown', 'anchor': 'specific-api'}
    for markup in ('<html>Please log in</html>', 'specific-api but nothing relevant'):
        result = monitor.collect(lambda url: markup, sources=[source])
        assert result['sources']['test']['error']
        assert result['sources']['test']['rules'] == []


def test_soft_wrapped_markdown_requirement_changes_are_detected(tmp_path):
    source = {'id': 'test', 'url': 'https://docs.example.test/api',
              'format': 'markdown', 'anchor': 'specific-api'}
    old = ('# specific-api\n\nTo leave room for required headers, keep\n'
           'the custom header budget below 60 KiB.\n\n'
           'Send X-Client-Request-Id\nfor request tracking.\n')
    first = monitor.collect(lambda url: old, sources=[source])
    assert any('60 KiB' in rule for rule in first['sources']['test']['rules'])
    assert any('X-Client-Request-Id' in rule for rule in first['sources']['test']['rules'])
    path = tmp_path / 'state.json'
    check(path, first, [])
    changed = monitor.collect(lambda url: old.replace('60 KiB', '40 KiB'), sources=[source])
    messages = []
    assert check(path, changed, messages) == 0
    assert len(messages) == 1
    assert '40 KiB' in messages[0]


def test_fetch_document_bounds_upstream_response_read(monkeypatch):
    import io
    reads = []

    class Response(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            return super().read(size)

    response = Response(b'x' * (monitor.MAX_BODY_BYTES + 10))
    monkeypatch.setattr(monitor.urllib.request, 'urlopen', lambda *a, **kw: response)
    with pytest.raises(ValueError):
        monitor.fetch_document('https://docs.example.test/api')
    assert reads == [monitor.MAX_BODY_BYTES + 1]


def test_collection_preserves_rule_context_and_detects_broken_pricing():
    source = {'id': 'opencode-zen', 'url': 'https://docs.example.test/models',
              'format': 'html', 'anchor': 'pricing', 'pricing': True}
    fixture = (Path(__file__).parent / 'fixtures' / 'opencode_console_models.html').read_text()
    good = '<h2>Pricing</h2><p>Free-tier chat requires <code>OpenCode</code>.</p>' + fixture
    result = monitor.collect(lambda url: good, sources=[source])
    assert result['sources']['opencode-zen']['error'] is None
    assert 'Free-tier chat requires OpenCode.' in result['sources']['opencode-zen']['rules']
    broken = good.replace('<th>Input</th>', '<th>Changed column</th>')
    result = monitor.collect(lambda url: broken, sources=[source])
    assert result['sources']['opencode-zen']['error']


def test_sendmail_nonzero_exit_propagates(monkeypatch):
    calls = []

    def send(argv, **kwargs):
        calls.append((argv, kwargs))
        raise subprocess.CalledProcessError(75, argv)

    monkeypatch.setattr(monitor.subprocess, 'run', send)
    with pytest.raises(subprocess.CalledProcessError):
        monitor.send_notification('A provider rule changed.', 'owner@example.test')
    argv, kwargs = calls[0]
    assert argv == ['/usr/sbin/sendmail', '-t']
    assert kwargs['check'] is True
    assert b'A provider rule changed.' in kwargs['input']


def test_collect_from_container_runs_committed_source_without_file_copy(monkeypatch):
    def run(argv, **kwargs):
        assert argv == ['docker', 'exec', '-i', 'gateway', 'python', '-', '--collect']
        assert 'def collect(' in kwargs['input']
        assert kwargs['check'] is True
        return subprocess.CompletedProcess(argv, 0, json.dumps(report(['Auth.'])), '')

    monkeypatch.setattr(monitor.subprocess, 'run', run)
    assert monitor.collect_from_container('gateway') == report(['Auth.'])
