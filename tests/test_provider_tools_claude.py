"""Tests for Claude provider tool-calling — verifies native pass-through and response mapping."""
from __future__ import annotations
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ['ANTHROPIC_API_KEY'] = 'sk-test-key-stub'

from providers.claude import ClaudeClient


def _fake_response_tool_use():
    """Build an anthropic SDK-style response object with a ToolUseBlock."""
    block = MagicMock()
    block.type = 'tool_use'
    block.id = 'toolu_01ABCDEFGH'
    block.name = 'web_search'
    block.input = {'query': 'Ollama 0.24 release'}
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = 'tool_use'
    resp.usage = MagicMock(input_tokens=120, output_tokens=42,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return resp


def _fake_response_end_turn():
    block = MagicMock()
    block.type = 'text'
    block.text = 'All done.'
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = 'end_turn'
    resp.usage = MagicMock(input_tokens=10, output_tokens=5,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return resp


def test_claude_tool_use_response_mapped_to_normalized_schema():
    with patch('anthropic.Anthropic'):
        client = ClaudeClient({'api_key': 'sk-test'})
    client.client = MagicMock()
    client.client.messages.create.return_value = _fake_response_tool_use()

    tools = [{'name': 'web_search',
              'description': 'search',
              'input_schema': {'type': 'object', 'properties': {'query': {'type': 'string'}}}}]

    out = client.create_message('claude-sonnet-4-6',
                                [{'role': 'user', 'content': 'find Ollama news'}],
                                max_tokens=500, tools=tools)

    assert out['stop_reason'] == 'tool_use'
    assert out['tool_calls'] == [{'id': 'toolu_01ABCDEFGH',
                                  'name': 'web_search',
                                  'input': {'query': 'Ollama 0.24 release'}}]
    kwargs = client.client.messages.create.call_args.kwargs
    assert kwargs['tools'] == tools, "tools must be passed natively to anthropic SDK"


def test_claude_end_turn_response_has_no_tool_calls():
    with patch('anthropic.Anthropic'):
        client = ClaudeClient({'api_key': 'sk-test'})
    client.client = MagicMock()
    client.client.messages.create.return_value = _fake_response_end_turn()

    out = client.create_message('claude-sonnet-4-6', [{'role': 'user', 'content': 'hi'}])

    assert out['stop_reason'] == 'end_turn'
    assert out['tool_calls'] == []
    assert out['content'] == [{'text': 'All done.'}]


def test_claude_backward_compat_no_tools_param():
    """Existing callers that don't pass tools still work."""
    with patch('anthropic.Anthropic'):
        client = ClaudeClient({'api_key': 'sk-test'})
    client.client = MagicMock()
    client.client.messages.create.return_value = _fake_response_end_turn()

    out = client.create_message('claude-sonnet-4-6', [{'role': 'user', 'content': 'hi'}])

    assert 'content' in out and 'usage' in out
    kwargs = client.client.messages.create.call_args.kwargs
    assert 'tools' not in kwargs, "tools must NOT be sent when caller did not pass any"
