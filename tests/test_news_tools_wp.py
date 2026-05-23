"""Tests for publish_to_wordpress — verifies REST API contract + idempotency + tag/category lookup."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('WORDPRESS_URL', 'https://wolfinisoftware.de')
os.environ.setdefault('WORDPRESS_USER', 'news-agent')
os.environ.setdefault('WORDPRESS_APP_PASSWORD', 'test-pwd')
os.environ.setdefault('WORDPRESS_CATEGORY', 'AI-News')
os.environ.setdefault('WORDPRESS_STATUS', 'publish')
os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)


def _resp(status_code: int, json_data):
    r = MagicMock()
    r.status_code = status_code
    r.raise_for_status = MagicMock()
    r.json = MagicMock(return_value=json_data)
    return r


def test_publish_creates_post_with_tags_and_category():
    """Happy path: no existing post → create category if missing → create new tags → POST /posts."""
    from agents.news import tools
    tools._wp_cache_reset()  # ensure tests are independent

    def fake_get(url, **kwargs):
        if 'users/me' in url:
            return _resp(200, {'id': 12, 'name': 'News Agent'})
        if '/posts' in url:
            return _resp(200, [])  # no existing post for idempotency check
        if '/categories' in url:
            return _resp(200, [{'id': 7, 'name': 'AI-News', 'slug': 'ai-news'}])
        if '/tags' in url:
            return _resp(200, [{'id': 1, 'name': 'Ollama', 'slug': 'ollama'}])
        raise AssertionError(f'unexpected GET {url}')

    posted = {}
    def fake_post(url, json=None, auth=None, **kwargs):
        if '/posts' in url:
            posted['payload'] = json
            return _resp(201, {'id': 999, 'link': 'https://wolfinisoftware.de/?p=999'})
        if '/tags' in url:
            return _resp(201, {'id': 42, 'name': json['name']})
        raise AssertionError(f'unexpected POST {url}')

    with patch('agents.news.tools.requests.get', side_effect=fake_get), \
         patch('agents.news.tools.requests.post', side_effect=fake_post):
        out = tools.publish_to_wordpress(
            title='Daily LLM Roundup',
            body_html='<p>Hi</p>',
            tags=['Ollama', 'llama.cpp', 'NOT_IN_ALLOWLIST'],
        )

    assert out == {'post_id': 999, 'url': 'https://wolfinisoftware.de/?p=999'}
    assert posted['payload']['title'] == 'Daily LLM Roundup'
    assert posted['payload']['content'] == '<p>Hi</p>'
    assert posted['payload']['status'] == 'publish'
    assert 7 in posted['payload']['categories']
    # 'NOT_IN_ALLOWLIST' must be dropped, Ollama (id=1) must be in tags, llama.cpp newly created (id=42)
    assert 1 in posted['payload']['tags']
    assert 42 in posted['payload']['tags']


def test_publish_is_idempotent_when_post_exists_today():
    from agents.news import tools
    tools._wp_cache_reset()

    existing_post = {'id': 555, 'link': 'https://wolfinisoftware.de/?p=555', 'title': {'rendered': 'Daily LLM Roundup'}}

    def fake_get(url, **kwargs):
        if 'users/me' in url:
            return _resp(200, {'id': 12})
        if '/posts' in url:
            return _resp(200, [existing_post])
        raise AssertionError(f'unexpected GET {url}')

    with patch('agents.news.tools.requests.get', side_effect=fake_get), \
         patch('agents.news.tools.requests.post') as mock_post:
        out = tools.publish_to_wordpress(title='Daily LLM Roundup', body_html='<p>x</p>')

    assert out == {'post_id': 555, 'url': 'https://wolfinisoftware.de/?p=555'}
    mock_post.assert_not_called()


def test_publish_returns_error_on_4xx():
    from agents.news import tools
    import requests as r
    tools._wp_cache_reset()

    def fake_get(url, **kwargs):
        if 'users/me' in url:
            return _resp(200, {'id': 12})
        if '/posts' in url:
            return _resp(200, [])
        if '/categories' in url:
            return _resp(200, [{'id': 7, 'name': 'AI-News'}])
        raise AssertionError(f'unexpected GET {url}')

    err = r.HTTPError(response=MagicMock(status_code=400, text='bad request'))
    def fake_post(url, **kwargs):
        m = MagicMock()
        m.raise_for_status = MagicMock(side_effect=err)
        return m

    with patch('agents.news.tools.requests.get', side_effect=fake_get), \
         patch('agents.news.tools.requests.post', side_effect=fake_post):
        out = tools.publish_to_wordpress(title='X', body_html='<p>x</p>')

    assert 'error' in out


def test_dry_run_does_not_post():
    from agents.news import tools
    tools._wp_cache_reset()

    with patch('agents.news.tools.requests.get') as mock_get, \
         patch('agents.news.tools.requests.post') as mock_post:
        out = tools.publish_to_wordpress(title='X', body_html='<p>x</p>',
                                         tags=['Ollama'], dry_run=True)

    assert out['dry_run'] is True
    assert out['title'] == 'X'
    mock_get.assert_not_called()
    mock_post.assert_not_called()
