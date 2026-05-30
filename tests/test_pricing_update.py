# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for opencode.ai Zen pricing fetcher."""

from cli import _parse_opencode_pricing


def test_parse_sample_table():
    html = '''<html><body>
<h2 id="pricing"><a href="#pricing">Pricing</a></h2>
<table><thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Cached Read</th><th>Cached Write</th></tr></thead><tbody>
<tr><td>GPT 5.4 Mini</td><td>$0.75</td><td>$4.50</td><td>$0.075</td><td>-</td></tr>
<tr><td>Claude Haiku 4.5</td><td>$1.00</td><td>$5.00</td><td>$0.10</td><td>$1.25</td></tr>
<tr><td>DeepSeek V4 Flash Free</td><td>Free</td><td>Free</td><td>Free</td><td>-</td></tr>
</tbody></table>
</body></html>'''
    data = _parse_opencode_pricing(html)
    assert 'opencode::gpt-5.4-mini' in data
    assert data['opencode::gpt-5.4-mini']['in'] == 0.75
    assert data['opencode::gpt-5.4-mini']['out'] == 4.50
    assert 'opencode::claude-haiku-4.5' in data
    assert data['opencode::claude-haiku-4.5']['out'] == 5.0


def test_free_models_fallback():
    html = '''<html><body><h2 id="pricing">Pricing</h2><table></table></body></html>'''
    data = _parse_opencode_pricing(html)
    assert 'opencode::big-pickle' in data
    assert data['opencode::big-pickle']['out'] == 0.0
    assert 'opencode::deepseek-v4-flash-free' in data


def test_empty_table_no_crash():
    html = '''<html><body><h2>Other</h2></body></html>'''
    import pytest
    with pytest.raises(ValueError, match='Pricing table not found'):
        _parse_opencode_pricing(html)
