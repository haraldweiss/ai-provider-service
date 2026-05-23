"""Tests for web_fetch tool."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)


def test_web_fetch_extracts_main_text():
    from agents.news.tools import web_fetch

    html = b'<html><body><nav>menu</nav><article><h1>Hi</h1><p>Body text here that is long enough to survive trafilatura extraction heuristics for sure.</p></article></body></html>'
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.content = html
    fake.headers = {'Content-Type': 'text/html'}

    with patch('agents.news.tools.requests.get', return_value=fake):
        out = web_fetch(url='https://example.com/post')

    assert 'text' in out
    assert 'Body text here' in out['text']
    assert out['url'] == 'https://example.com/post'


def test_web_fetch_returns_error_on_4xx():
    from agents.news.tools import web_fetch
    import requests as r

    fake = MagicMock()
    err = r.HTTPError(response=MagicMock(status_code=404))
    fake.raise_for_status = MagicMock(side_effect=err)

    with patch('agents.news.tools.requests.get', return_value=fake):
        out = web_fetch(url='https://example.com/missing')

    assert 'error' in out
    assert out['url'] == 'https://example.com/missing'


def test_web_fetch_caps_text_length():
    from agents.news.tools import web_fetch

    big_text = 'word ' * 10000
    big_html = f'<html><body><article><p>{big_text}</p></article></body></html>'.encode()
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.content = big_html
    fake.headers = {'Content-Type': 'text/html'}

    with patch('agents.news.tools.requests.get', return_value=fake):
        out = web_fetch(url='https://example.com/big')

    assert len(out['text']) <= 20_500, "text must be capped around 20k chars"
    assert out['text'].endswith('… [truncated]')
