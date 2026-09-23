# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the daily provider-documentation watcher."""

import json

import pytest

import provider_docs as pd


def test_module_exposes_sources():
    assert pd.PROVIDER_DOC_SOURCES


def test_html_to_text_strips_markup_and_scripts():
    markup = '<html><head><style>x{}</style></head><body>'
    markup += '<script>var x=1;</script><h1>Send a stable session ID</h1>'
    markup += '<p>Use <code>x-opencode-session</code></p></body></html>'
    text = pd.html_to_text(markup)
    assert 'Send a stable session ID' in text
    assert 'x-opencode-session' in text
    assert 'var x=1' not in text
    assert '<h1>' not in text


def test_extract_rule_lines_filters_by_watch_terms():
    text = (
        'Some unrelated sentence about pricing.\n'
        'Send a stable session ID in x-opencode-session for each conversation.\n'
        'HTTP-Referer is optional.\n'
        'This line mentions nothing relevant.\n'
    )
    rules = pd.extract_rule_lines(text)
    assert any('x-opencode-session' in r for r in rules)
    assert any('HTTP-Referer' in r for r in rules)
    assert not any('nothing relevant' in r for r in rules)


@pytest.fixture()
def snapshot_path(tmp_path, monkeypatch):
    path = tmp_path / 'provider_docs_snapshot.json'
    monkeypatch.setattr(pd.Config, 'PROVIDER_DOCS_SNAPSHOT', str(path))
    return path


def _fetcher(pages):
    def _fetch(url, timeout=30):
        return pages[url]
    return _fetch


GO_URL = pd.PROVIDER_DOC_SOURCES[0]['url']
ZEN_URL = pd.PROVIDER_DOC_SOURCES[1]['url']
CLINE_AUTH_URL = pd.PROVIDER_DOC_SOURCES[2]['url']
CLINE_MODELS_URL = pd.PROVIDER_DOC_SOURCES[3]['url']


def _pages(go_text='Send x-opencode-session and your own user agent.'):
    return {
        GO_URL: f'<html><body><p>{go_text}</p></body></html>',
        ZEN_URL: '<html><body><p>Free models list.</p></body></html>',
        CLINE_AUTH_URL: '<html><body><p>HTTP-Referer and X-Title are optional.</p></body></html>',
        CLINE_MODELS_URL: '<html><body><p>provider/model-name ids.</p></body></html>',
    }


def test_run_check_first_run_seeds_without_changes(snapshot_path):
    result = pd.run_check(fetcher=_fetcher(_pages()))
    assert result['seeded'] is True
    assert result['changed'] is False
    saved = json.loads(snapshot_path.read_text())
    assert 'opencode-go' in saved['sources']
    assert any('x-opencode-session' in r for r in saved['sources']['opencode-go']['rules'])


def test_run_check_detects_rule_change(snapshot_path):
    pd.run_check(fetcher=_fetcher(_pages()))
    changed_pages = _pages(
        go_text='Send a stable session ID in x-opencode-session and X-Task-ID.'
    )
    result = pd.run_check(fetcher=_fetcher(changed_pages))
    assert result['changed'] is True
    go_diff = result['diff']['opencode-go']
    assert any('X-Task-ID' in r for r in go_diff['added'])
    assert any('own user agent' in r for r in go_diff['removed'])


def test_run_check_no_change_after_stable_refetch(snapshot_path):
    pages = _pages()
    pd.run_check(fetcher=_fetcher(pages))
    result = pd.run_check(fetcher=_fetcher(pages))
    assert result['changed'] is False
    assert result['errors'] == {}


def test_run_check_keeps_old_snapshot_on_fetch_failure(snapshot_path):
    pd.run_check(fetcher=_fetcher(_pages()))
    baseline = json.loads(snapshot_path.read_text())

    def failing(url, timeout=30):
        if url == GO_URL:
            raise OSError('network down')
        return _pages()[url]

    result = pd.run_check(fetcher=failing)
    assert 'opencode-go' in result['errors']
    assert result['changed'] is False
    saved = json.loads(snapshot_path.read_text())
    # Failed source keeps its previous snapshot instead of being dropped.
    assert saved['sources']['opencode-go'] == baseline['sources']['opencode-go']


def test_format_change_email_mentions_adjustment_targets():
    diff = {
        'opencode-go': {
            'label': 'OpenCode Go',
            'url': GO_URL,
            'added': ['Send X-Task-ID.'],
            'removed': [],
        }
    }
    body = pd.format_change_email(diff)
    assert 'OpenCode Go' in body
    assert '+ Send X-Task-ID.' in body
    assert 'AGENTS.md §3.12' in body
