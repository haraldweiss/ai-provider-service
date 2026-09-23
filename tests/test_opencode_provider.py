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
    # OpenCode Go requires clients to identify with their own UA, not the SDK's.
    assert kwargs['default_headers']['User-Agent'].startswith('ai-provider-service/')
    # The session header is per-conversation, not a global default.
    assert 'x-opencode-session' not in kwargs['default_headers']


def test_conversation_session_id_stable_across_turns():
    from providers.opencode import _conversation_session_id
    turn1 = [
        {'role': 'system', 'content': 'You are helpful.'},
        {'role': 'user', 'content': 'Hallo'},
    ]
    turn2 = turn1 + [
        {'role': 'assistant', 'content': 'Hi!'},
        {'role': 'user', 'content': 'Wie geht es dir?'},
    ]
    assert _conversation_session_id(turn1) == _conversation_session_id(turn2)


def test_conversation_session_id_differs_between_conversations():
    from providers.opencode import _conversation_session_id
    a = _conversation_session_id([{'role': 'user', 'content': 'Wie ist das Wetter?'}])
    b = _conversation_session_id([{'role': 'user', 'content': 'Erzähl mir einen Witz.'}])
    assert a != b


def test_conversation_session_id_handles_multimodal_content():
    from providers.opencode import _conversation_session_id
    content = [
        {'type': 'text', 'text': 'Was ist das?'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}},
    ]
    assert _conversation_session_id([{'role': 'user', 'content': content}]) != 'ai-provider-service'


@patch('providers.opencode.OpenAI')
def test_opencode_create_message_sends_per_conversation_session(mock_openai):
    from providers.opencode import OpencodeClient, _conversation_session_id
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='hi back'))]
    fake_response.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    create = mock_openai.return_value.chat.completions.create
    create.return_value = fake_response

    messages = [{'role': 'user', 'content': 'hi'}]
    OpencodeClient({'api_key': 'sk-test'}).create_message('gpt-5', messages, 50)

    extra_headers = create.call_args.kwargs['extra_headers']
    assert extra_headers['x-opencode-session'] == _conversation_session_id(messages)


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


def _fake_models_client(mock_openai, ids):
    """Prime the mocked OpenAI client with a models.list() result."""
    fake_models = [MagicMock(id=mid) for mid in ids]
    mock_openai.return_value.models.list.return_value = MagicMock(data=fake_models)


@patch('providers.opencode.OpencodeClient.get_free_models', return_value=['a-free', 'big-pickle'])
@patch('providers.opencode.OpenAI')
def test_opencode_free_only_hides_models_by_default(mock_openai, _mock_free, monkeypatch):
    """Free tier is client-locked upstream (2026-09-18) → free-only mode
    advertises nothing so clients stop picking dead opencode/* models."""
    monkeypatch.delenv('OPENCODE_ADVERTISE_FREE_MODELS', raising=False)
    from providers.opencode import OpencodeClient
    _fake_models_client(mock_openai, ['a-free', 'big-pickle', 'paid-pro'])
    c = OpencodeClient({'api_key': 'sk-test', '_free_only': True})
    assert c.get_models() == []


@patch('providers.opencode.OpencodeClient.get_free_models', return_value=['a-free', 'big-pickle'])
@patch('providers.opencode.OpenAI')
def test_opencode_free_only_restored_via_env(mock_openai, _mock_free, monkeypatch):
    """OPENCODE_ADVERTISE_FREE_MODELS=1 restores the old free-list behavior."""
    monkeypatch.setenv('OPENCODE_ADVERTISE_FREE_MODELS', '1')
    from providers.opencode import OpencodeClient
    _fake_models_client(mock_openai, ['a-free', 'big-pickle', 'paid-pro'])
    c = OpencodeClient({'api_key': 'sk-test', '_free_only': True})
    assert c.get_models() == ['a-free', 'big-pickle']


@patch('providers.opencode.OpencodeClient.get_free_models', return_value=['a-free'])
@patch('providers.opencode.OpenAI')
def test_opencode_personal_key_models_unaffected(mock_openai, _mock_free, monkeypatch):
    """Personal-key (non-free-only) clients keep the full list — paid models
    are not covered by the upstream free-tier lock."""
    monkeypatch.delenv('OPENCODE_ADVERTISE_FREE_MODELS', raising=False)
    from providers.opencode import OpencodeClient
    _fake_models_client(mock_openai, ['a-free', 'paid-pro'])
    c = OpencodeClient({'api_key': 'sk-personal'})
    assert c.get_models() == ['a-free', 'paid-pro']
