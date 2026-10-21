"""The host cron monitor must not replace good state with upstream errors."""
import json
from pathlib import Path
import importlib.util

import pytest


def load_monitor():
    path = Path(__file__).parents[1] / 'scripts' / 'check_openrouter_models.py'
    spec = importlib.util.spec_from_file_location('openrouter_monitor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def model(model_id='vendor/free', price='0'):
    return {'id': model_id, 'name': model_id, 'context_length': 1000,
            'pricing': {'prompt': price, 'completion': price},
            'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
            'supported_parameters': ['tools', 'max_tokens']}


@pytest.mark.parametrize('payload', [
    {'error': {'code': 401}}, {'data': []}, {'data': [{}]},
    {'data': [model(), model()]}, {'data': [model(price='NaN')]},
])
def test_invalid_catalog_does_not_become_a_baseline(payload):
    monitor = load_monitor()
    with pytest.raises(ValueError):
        monitor.normalize_catalog(payload)


def test_catalog_detects_price_and_capability_changes_but_ignores_order():
    monitor = load_monitor()
    before = monitor.normalize_catalog({'data': [model()]})
    reordered = model()
    reordered['supported_parameters'].reverse()
    assert monitor.normalize_catalog({'data': [reordered]}) == before
    changed = model(price='0.01')
    changed['context_length'] = 2000
    assert monitor.catalog_diff(before, monitor.normalize_catalog({'data': [changed]})) == {
        'added': [], 'removed': [], 'changed': ['vendor/free']}


def test_router_dynamic_price_sentinel_is_catalogued_but_never_probed():
    monitor = load_monitor()
    catalog = monitor.normalize_catalog({'data': [model('openrouter/auto', '-1')]})
    def forbidden(_):
        pytest.fail('A dynamically priced router must never be probed')
    assert monitor.check_models(catalog, ['openrouter/auto'], forbidden) == {
        'openrouter/auto': 'paid_not_probed'}


@pytest.mark.parametrize('status,body,want', [
    (404, {'error': {'message': 'secret upstream content'}}, 'http_404'),
    (200, {'error': {'message': 'secret upstream content'}}, 'upstream_error'),
    (200, {'choices': [{'message': {'content': ''}, 'finish_reason': 'stop'}]}, 'empty_content'),
    (200, {'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'length'}]}, 'finish_length'),
    (200, {'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]}, 'ok'),
])
def test_completion_probe_checks_body_and_finish_without_leaking_it(status, body, want):
    monitor = load_monitor()
    class Response:
        status_code = status
        def json(self):
            return body
    result = monitor.completion_status(Response())
    assert result == want
    assert 'secret' not in result


def test_failed_collection_preserves_snapshot_and_reports_only_exception_type(tmp_path):
    monitor = load_monitor()
    path = tmp_path / 'state.json'
    path.write_text('{"previous":"good"}')
    notices = []
    def fail():
        raise RuntimeError('API key must never appear')
    assert monitor.run_check(path, fail, notices.append) == 1
    assert path.read_text() == '{"previous":"good"}'
    assert len(notices) == 1
    assert 'RuntimeError' in notices[0]
    assert 'API key' not in notices[0]


def test_first_run_checks_operational_models_and_unchanged_run_stays_quiet(tmp_path):
    monitor = load_monitor()
    path = tmp_path / 'state.json'
    result = {'models': monitor.normalize_catalog({'data': [model()]}),
              'probes': {'vendor/free': 'ok'}}
    notices = []
    assert monitor.run_check(path, lambda: result, notices.append) == 0
    assert monitor.run_check(path, lambda: result, notices.append) == 0
    assert notices == []
    assert json.loads(path.read_text())['probes'] == {'vendor/free': 'ok'}


def test_failed_probe_alerts_on_first_run_and_recovery_alerts_once(tmp_path):
    monitor = load_monitor()
    path = tmp_path / 'state.json'
    result = {'models': monitor.normalize_catalog({'data': [model()]}),
              'probes': {'vendor/free': 'http_404'}}
    notices = []
    assert monitor.run_check(path, lambda: result, notices.append) == 1
    assert 'vendor/free: http_404' in notices[0]
    result['probes']['vendor/free'] = 'ok'
    assert monitor.run_check(path, lambda: result, notices.append) == 0
    assert 'recovered' in notices[1].lower()
    assert monitor.run_check(path, lambda: result, notices.append) == 0
    assert len(notices) == 2


def test_notification_failure_keeps_old_baseline_for_retry(tmp_path):
    monitor = load_monitor()
    path = tmp_path / 'state.json'
    before = {'models': monitor.normalize_catalog({'data': [model()]}), 'probes': {}}
    path.write_text(json.dumps(before))
    after = {'models': monitor.normalize_catalog({'data': [model('vendor/new')]}), 'probes': {}}
    def fail(_):
        raise OSError('mail transport failure')
    assert monitor.run_check(path, lambda: after, fail) == 1
    assert json.loads(path.read_text()) == before


def test_paid_model_is_not_probed_and_transient_failure_is_rechecked():
    monitor = load_monitor()
    catalog = monitor.normalize_catalog({'data': [model(), model('vendor/paid', '1')]})
    calls = []
    def probe(mid):
        calls.append(mid)
        return 'http_429' if len(calls) == 1 else 'ok'
    statuses = monitor.check_models(catalog, ['vendor/free', 'vendor/paid', 'gone'], probe)
    assert calls == ['vendor/free', 'vendor/free']
    assert statuses == {'vendor/free': 'ok', 'vendor/paid': 'paid_not_probed', 'gone': 'missing_from_catalog'}


def test_probe_budget_only_counts_eligible_models_and_does_not_abort_catalog_check():
    monitor = load_monitor()
    rows = [model()] + [model('paid/' + str(i), '1') for i in range(9)]
    catalog = monitor.normalize_catalog({'data': rows})
    calls = []
    def probe(mid):
        calls.append(mid)
        return 'ok'
    statuses = monitor.check_models(catalog, list(catalog) + ['gone'], probe)
    assert calls == ['vendor/free']
    assert statuses['gone'] == 'missing_from_catalog'
    free = monitor.normalize_catalog({'data': [model('free/' + str(i)) for i in range(9)]})
    calls.clear()
    statuses = monitor.check_models(free, list(free), probe)
    assert len(calls) == 8
    assert statuses['free/8'] == 'probe_budget_exceeded'


def test_collector_reports_configured_models_hidden_by_gateway(tmp_path, monkeypatch):
    import requests
    from config import Config
    from sqlalchemy import create_engine, text
    monitor = load_monitor()
    uri = 'sqlite:///' + str(tmp_path / 'configs.db')
    engine = create_engine(uri)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE provider_configs (fallback_provider TEXT, fallback_model TEXT)'))
    engine.dispose()
    monkeypatch.setattr(Config, 'SQLALCHEMY_DATABASE_URI', uri)
    monkeypatch.setattr(Config, 'MEMORY_FREE_MODELS', ['openrouter::vendor/free'])
    monkeypatch.setattr(Config, 'OPENROUTER_BASE_URL', 'https://upstream.invalid/v1')
    class Response:
        status_code = 200
        def __init__(self, body):
            self.body = body
        def raise_for_status(self):
            pass
        def json(self):
            return self.body
    def get(url, **kwargs):
        if url == 'https://upstream.invalid/v1/models':
            return Response({'data': [model()]})
        assert url.startswith('http://127.0.0.1:8767/v1/models?')
        return Response({'data': []})
    def post(url, **kwargs):
        assert url == 'https://upstream.invalid/v1/chat/completions'
        assert kwargs['json']['model'] == 'vendor/free'
        return Response({'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(requests, 'get', get)
    monkeypatch.setattr(requests, 'post', post)
    result = monitor.collect()
    assert result['probes'] == {'vendor/free': 'ok'}
    assert result['gateway_missing_configured'] == ['vendor/free']
    notices = []
    assert monitor.run_check(tmp_path / 'snapshot.json', lambda: result, notices.append) == 1
    assert 'Missing configured gateway IDs:\nvendor/free' in notices[0]
