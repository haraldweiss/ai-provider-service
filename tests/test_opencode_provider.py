# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OpencodeClient + factory registration."""

from unittest.mock import MagicMock, patch
import pytest
from providers import get_client, PROVIDER_REGISTRY


def test_opencode_registered():
    assert 'opencode' in PROVIDER_REGISTRY


def test_opencode_is_system_provider_with_optional_api_key():
    """opencode ist System-Provider (Free-Tier über zentralen OPENCODE_API_KEY).
    Ein eigener api_key ist optional und schaltet Paid-Modelle frei — er ist
    daher NICHT in 'requires', sondern in 'optional'."""
    meta = PROVIDER_REGISTRY['opencode']
    assert meta['system'] is True
    assert 'api_key' not in meta['requires']
    assert 'api_key' in meta['optional']


def test_factory_returns_opencode_client():
    client = get_client('opencode', {'api_key': 'sk-test'})
    assert client.__class__.__name__ == 'OpencodeClient'


def test_opencode_raises_without_api_key():
    with pytest.raises(ValueError, match='api_key'):
        get_client('opencode', {})


@patch('providers.opencode.OpenAI')
def test_opencode_uses_default_base_url(mock_openai):
    from providers.opencode import OpencodeClient
    OpencodeClient({'api_key': 'sk-test'})
    args, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://opencode.ai/zen/v1'
    assert kwargs['api_key'] == 'sk-test'


@patch('providers.opencode.OpenAI')
def test_opencode_respects_custom_endpoint(mock_openai):
    from providers.opencode import OpencodeClient
    OpencodeClient({'api_key': 'sk-test', 'api_endpoint': 'https://example.org/v1'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://example.org/v1'


@patch('providers.opencode.OpenAI')
def test_opencode_create_message_returns_claude_format(mock_openai):
    from providers.opencode import OpencodeClient
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='hi back'))]
    fake_response.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    mock_openai.return_value.chat.completions.create.return_value = fake_response

    c = OpencodeClient({'api_key': 'sk-test'})
    out = c.create_message('gpt-5', [{'role': 'user', 'content': 'hi'}], 50)

    assert out == {
        'content': [{'text': 'hi back'}],
        'usage': {'input_tokens': 10, 'output_tokens': 3},
    }
