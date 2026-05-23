"""Tests for Ollama provider tool-calling — verifies tool format conversion in both directions."""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

from providers.ollama import OllamaClient


def _ollama_tool_call_response():
    """Simulate an /api/chat response where Ollama returned a tool_call."""
    return {
        'message': {
            'role': 'assistant',
            'content': '',
            'tool_calls': [
                {'function': {'name': 'web_search',
                              'arguments': {'query': 'llama.cpp release'}}},
            ],
        },
        'done': True,
        'done_reason': 'stop',
        'prompt_eval_count': 200,
        'eval_count': 30,
    }


def _ollama_end_turn_response():
    return {
        'message': {'role': 'assistant', 'content': 'Done.'},
        'done': True,
        'done_reason': 'stop',
        'prompt_eval_count': 10,
        'eval_count': 5,
    }


def _fake_post_factory(payload_response):
    def _fake_post(url, json=None, timeout=None):
        # Capture the outgoing payload so the test can inspect tool format conversion.
        _fake_post.last_payload = json
        r = MagicMock()
        r.raise_for_status = MagicMock()
        r.json = MagicMock(return_value=payload_response)
        return r
    _fake_post.last_payload = None
    return _fake_post


def test_ollama_tools_mapped_to_openai_function_format():
    fake_post = _fake_post_factory(_ollama_end_turn_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    tools = [{'name': 'web_search',
              'description': 'search',
              'input_schema': {'type': 'object',
                               'properties': {'query': {'type': 'string'}},
                               'required': ['query']}}]

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        client.create_message('qwen3.6:latest',
                              [{'role': 'user', 'content': 'find news'}],
                              tools=tools)

    payload = fake_post.last_payload
    assert 'tools' in payload, "tools must be in outgoing /api/chat payload"
    assert payload['tools'] == [{
        'type': 'function',
        'function': {
            'name': 'web_search',
            'description': 'search',
            'parameters': {'type': 'object',
                           'properties': {'query': {'type': 'string'}},
                           'required': ['query']},
        }
    }]


def test_ollama_tool_call_response_mapped_to_normalized_schema():
    fake_post = _fake_post_factory(_ollama_tool_call_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        out = client.create_message('qwen3.6:latest',
                                    [{'role': 'user', 'content': 'find news'}],
                                    tools=[{'name': 'web_search', 'input_schema': {}}])

    assert out['stop_reason'] == 'tool_use'
    assert len(out['tool_calls']) == 1
    tc = out['tool_calls'][0]
    assert tc['name'] == 'web_search'
    assert tc['input'] == {'query': 'llama.cpp release'}
    assert tc['id']  # synthesized ID must be non-empty


def test_ollama_end_turn_has_empty_tool_calls():
    fake_post = _fake_post_factory(_ollama_end_turn_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        out = client.create_message('qwen3.6:latest',
                                    [{'role': 'user', 'content': 'hi'}])

    assert out['stop_reason'] == 'end_turn'
    assert out['tool_calls'] == []


def test_ollama_backward_compat_no_tools():
    fake_post = _fake_post_factory(_ollama_end_turn_response())
    client = OllamaClient({'api_endpoint': 'http://127.0.0.1:11434'})

    with patch('providers.ollama.requests.post', side_effect=fake_post):
        out = client.create_message('qwen3.6:latest',
                                    [{'role': 'user', 'content': 'hi'}])

    assert 'tools' not in fake_post.last_payload, "tools must NOT appear in payload when caller passed none"
    assert out['content'] == [{'text': 'Done.'}]
