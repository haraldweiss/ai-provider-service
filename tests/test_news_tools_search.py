"""Tests for web_search tool (SearXNG-backed)."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('SEARXNG_URL', 'http://127.0.0.1:8888')
os.environ.setdefault('SERVICE_TOKEN', 'test-token')
os.environ.setdefault('ENCRYPTION_KEY', 'X' * 44)


def _fake_searxng_response():
    return {
        'results': [
            {'title': 'Ollama v0.24 Release',
             'url': 'https://github.com/ollama/ollama/releases/tag/v0.24.0',
             'content': 'New features: Codex App, ...'},
            {'title': 'llama.cpp speculative decoding',
             'url': 'https://github.com/ggerganov/llama.cpp/pull/12345',
             'content': 'Adds spec-dec for Apple Silicon ...'},
        ],
    }


def test_web_search_returns_normalized_hits():
    from agents.news.tools import web_search

    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json = MagicMock(return_value=_fake_searxng_response())

    with patch('agents.news.tools.requests.get', return_value=fake) as mock_get:
        hits = web_search(query='Ollama 0.24', max_results=5)

    assert len(hits) == 2
    assert hits[0] == {
        'title': 'Ollama v0.24 Release',
        'url': 'https://github.com/ollama/ollama/releases/tag/v0.24.0',
        'snippet': 'New features: Codex App, ...',
    }
    args, kwargs = mock_get.call_args
    assert kwargs['params']['q'] == 'Ollama 0.24'
    assert kwargs['params']['format'] == 'json'


def test_web_search_caps_max_results():
    from agents.news.tools import web_search

    big_response = {'results': [{'title': f't{i}', 'url': f'http://x/{i}', 'content': 's'} for i in range(50)]}
    fake = MagicMock()
    fake.raise_for_status = MagicMock()
    fake.json = MagicMock(return_value=big_response)

    with patch('agents.news.tools.requests.get', return_value=fake):
        hits = web_search(query='x', max_results=3)

    assert len(hits) == 3


def test_web_search_returns_error_dict_on_failure():
    from agents.news.tools import web_search

    with patch('agents.news.tools.requests.get', side_effect=ConnectionError('refused')):
        result = web_search(query='x')

    assert isinstance(result, dict), "single-error result is a dict, not a list"
    assert 'error' in result
    assert 'search backend unavailable' in result['error'].lower()
