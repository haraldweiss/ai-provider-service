# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for opencode.ai Zen pricing fetcher."""

from cli import _parse_opencode_pricing
import pytest
from html import escape


def _with_model_ids(pricing_html, model_ids):
    rows = ''.join(
        f'<tr><td>{escape(name)}</td><td>{escape(model_id)}</td></tr>'
        for name, model_id in model_ids.items()
    )
    return '<table><tr><th>Model</th><th>Model ID</th></tr>' + rows + '</table>' + pricing_html


def test_unparseable_table_is_rejected_instead_of_free_only_override():
    html = ('<table><th>Model</th><th>Input</th><th>Output</th>'
            '<tr><td>Paid</td><td>USD 9</td><td>USD 19</td></tr></table>')
    with pytest.raises(ValueError, match='no parseable'):
        _parse_opencode_pricing(_with_model_ids(html, {'Paid': 'paid'}))


def test_parse_sample_table():
    html = '''<html><body>
<h2 id="pricing"><a href="#pricing">Pricing</a></h2>
<table><thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th><th>Cached Write</th></tr></thead><tbody>
<tr><td>GPT 5.4 Mini</td><td>$0.75</td><td>$4.50</td><td>$0.075</td><td>-</td></tr>
<tr><td>Claude Haiku 4.5</td><td>$1.00</td><td>$5.00</td><td>$0.10</td><td>$1.25</td></tr>
<tr><td>DeepSeek V4 Flash Free</td><td>Free</td><td>Free</td><td>Free</td><td>-</td></tr>
</tbody></table>
</body></html>'''
    data = _parse_opencode_pricing(_with_model_ids(html, {
        'GPT 5.4 Mini': 'gpt-5.4-mini',
        'Claude Haiku 4.5': 'claude-haiku-4-5',
        'DeepSeek V4 Flash Free': 'deepseek-v4-flash-free',
    }))
    assert 'opencode::gpt-5.4-mini' in data
    assert data['opencode::gpt-5.4-mini']['in'] == 0.75
    assert data['opencode::gpt-5.4-mini']['out'] == 4.50
    assert 'opencode::claude-haiku-4-5' in data
    assert data['opencode::claude-haiku-4-5']['out'] == 5.0


def test_free_models_in_table():
    html = '''<html><body>
<table><thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th></tr></thead><tbody>
<tr><td>Big Pickle</td><td>Free</td><td>Free</td><td>Free</td></tr>
</tbody></table>
</body></html>'''
    data = _parse_opencode_pricing(_with_model_ids(html, {'Big Pickle': 'big-pickle'}))
    assert 'opencode::big-pickle' in data
    assert data['opencode::big-pickle']['out'] == 0.0


def test_empty_table_no_crash():
    html = '''<html><body><h2>Other</h2></body></html>'''
    import pytest
    with pytest.raises(ValueError, match='Pricing table not found'):
        _parse_opencode_pricing(html)


def test_console_pricing_preserves_all_models_and_current_discounted_rates():
    from pathlib import Path

    html = (Path(__file__).parent / 'fixtures' / 'opencode_console_models.html').read_text()
    data = _parse_opencode_pricing(html)
    # 109 price rows collapse to 92 models because context tiers share an ID.
    assert len(data) == 92
    assert data['opencode::mistral-large-4'] == {'in': 0.68, 'out': 2.09}
    assert data['opencode::gemini-4-carbon'] == {'in': 2.0, 'out': 10.0}
    assert data['opencode::claude-haiku-4-5'] == {'in': 1.0, 'out': 5.0}
    assert 'opencode::claude-haiku-4.5' not in data
    assert 'opencode::deepseek-v4-flash-free' not in data
    assert data['opencode::deepseek-v4.1-flash-free'] == {'in': 0.0, 'out': 0.0}


def test_pricing_does_not_invent_absent_free_models():
    html = """<table><tr><th>Model</th><th>Input</th><th>Output</th></tr>
    <tr><td>Paid Model</td><td>$1</td><td>$2</td></tr></table>"""
    assert _parse_opencode_pricing(_with_model_ids(html, {'Paid Model': 'paid-model'})) == {
        'opencode::paid-model': {'in': 1.0, 'out': 2.0},
    }


@pytest.mark.parametrize('bad_cells', [
    '<td>Broken</td><td>USD 9</td><td>$19</td>',
    '<td>Broken</td><td>$9</td>',
    '<td>Broken</td><td>$NaN</td><td>$19</td>',
    '<td>Broken</td><td>$-1</td><td>$19</td>',
])
def test_partial_pricing_is_rejected(bad_cells):
    html = f"""<table><tr><th>Model</th><th>Input</th><th>Output</th></tr>
    <tr><td>Working</td><td>$1</td><td>$2</td></tr>
    <tr>{bad_cells}</tr></table>"""
    with pytest.raises(ValueError):
        _parse_opencode_pricing(_with_model_ids(html, {'Working': 'working', 'Broken': 'broken'}))


def test_pricing_ignores_other_tables_and_handles_inline_markup():
    html = """<table><tr><th>Input</th></tr><tr><td>Not pricing</td></tr></table>
    <table><tr><th><span>Model</span></th><th>Output</th><th>Input</th></tr>
    <tr><td><a href="/models/test">A &amp; B</a></td>
    <td><del>$9</del> <strong>$2.50</strong></td><td> $0.10 </td></tr></table>"""
    assert _parse_opencode_pricing(_with_model_ids(html, {'A & B': 'a-b'})) == {
        'opencode::a-b': {'in': 0.1, 'out': 2.5},
    }


def test_pricing_rejects_model_missing_from_endpoint_table():
    html = """<table><tr><th>Model</th><th>Model ID</th></tr>
    <tr><td>Known</td><td>actual-known-id</td></tr></table>
    <table><tr><th>Model</th><th>Input</th><th>Output</th></tr>
    <tr><td>Unknown</td><td>$1</td><td>$2</td></tr></table>"""
    with pytest.raises(ValueError, match='no endpoint ID'):
        _parse_opencode_pricing(html)


def test_fetch_console_pricing_from_canonical_url(monkeypatch):
    import io
    from pathlib import Path
    import cli

    fixture = (Path(__file__).parent / 'fixtures' / 'opencode_console_models.html').read_bytes()

    def urlopen(request, timeout):
        if request.full_url != 'https://opencode.ai/docs/console/models/':
            raise OSError('Old documentation route removed')
        return io.BytesIO(fixture)

    monkeypatch.setattr(cli.urllib.request, 'urlopen', urlopen)
    data = cli.fetch_opencode_pricing()
    assert len(data) == 92
    assert data['opencode::gemini-4-argon'] == {'in': 2.0, 'out': 10.0}


def test_cli_preserves_existing_override_on_partial_parse(tmp_path, monkeypatch):
    import io
    import cli
    from click.testing import CliRunner

    cache = tmp_path / 'pricing_overrides.json'
    baseline = '{"opencode::saved-model": {"in": 1.0, "out": 2.0}}'
    cache.write_text(baseline)
    monkeypatch.setattr(cli, '__file__', str(tmp_path / 'cli.py'))
    markup = b"""<table><tr><th>Model</th><th>Input</th><th>Output</th></tr>
    <tr><td>Valid</td><td>$1</td><td>$2</td></tr>
    <tr><td>Broken</td><td>USD 9</td><td>$19</td></tr></table>"""
    monkeypatch.setattr(cli.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(_with_model_ids(
        markup.decode(), {'Valid': 'valid', 'Broken': 'broken'}).encode()))
    result = CliRunner().invoke(cli.update_opencode_pricing_command)
    assert result.exit_code != 0
    assert cache.read_text() == baseline


@pytest.mark.parametrize('endpoint_table', [
    '',
    '<table><tr><th>Model</th><th>Model ID</th></tr></table>',
])
def test_missing_model_ids_preserves_existing_override(tmp_path, monkeypatch, endpoint_table):
    import io
    import cli
    from click.testing import CliRunner

    cache = tmp_path / 'pricing_overrides.json'
    baseline = '{"opencode::claude-haiku-4-5": {"in": 1.0, "out": 5.0}}'
    cache.write_text(baseline)
    monkeypatch.setattr(cli, '__file__', str(tmp_path / 'cli.py'))
    markup = endpoint_table + """<table><tr><th>Model</th><th>Input</th><th>Output</th></tr>
    <tr><td>Claude Haiku 4.5</td><td>$1</td><td>$5</td></tr></table>"""
    monkeypatch.setattr(cli.urllib.request, 'urlopen',
                        lambda *a, **kw: io.BytesIO(markup.encode()))
    result = CliRunner().invoke(cli.update_opencode_pricing_command)
    assert result.exit_code != 0
    assert cache.read_text() == baseline
