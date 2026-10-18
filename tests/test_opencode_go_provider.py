# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OpencodeGoClient (OpenCode Go / Go Plus subscription).

Context (AGENTS §7, 2026-10-18): OpenCode invalidated the Console API key with
its breach response, and the Zen free tier stays locked to the OpenCode app, so
the gateway's `opencode` provider can serve nothing. OpenCode Go (a paid,
subscription-backed catalog) does work and is wired up as its own provider.
"""

from unittest.mock import MagicMock, patch

import pytest
from providers import get_client, PROVIDER_REGISTRY


def test_opencode_go_registered_as_system_provider():
    meta = PROVIDER_REGISTRY['opencode_go']
    assert meta['system'] is True
    assert meta['personal_api_key'] is True
    # A personal key must be optional (server key funds the shared Go plan).
    assert 'api_key' not in meta['requires']
    assert 'api_key' in meta['optional']


def test_factory_returns_opencode_go_client():
    client = get_client('opencode_go', {'api_key': 'oc_sk_test'})
    assert client.__class__.__name__ == 'OpencodeGoClient'
    assert isinstance(client, __import__(
        'providers.opencode', fromlist=['OpencodeClient']).OpencodeClient)


@patch('providers.opencode.OpenAI')
def test_opencode_go_uses_v2_inference_endpoints(mock_openai, monkeypatch):
    """OpenCode v2 documents two bases for Go: chat under /inference/go/openai/v1
    and the model list at /inference/go/v1/models — not siblings, so the client
    must build a second client for the model list instead of reusing chat's."""
    import providers.opencode as opencode_module
    monkeypatch.setattr(opencode_module.Config, 'OPENCODE_GO_BASE_URL',
                        'https://opencode.ai/inference/go/openai/v1')
    monkeypatch.setattr(opencode_module.Config, 'OPENCODE_GO_MODELS_BASE_URL',
                        'https://opencode.ai/inference/go/v1')
    monkeypatch.setattr(opencode_module.Config, 'OPENCODE_BASE_URL',
                        'https://opencode.ai/zen/v1')

    client = opencode_module.OpencodeGoClient({'api_key': 'oc_sk_test'})

    assert mock_openai.call_count == 2
    chat_kwargs = mock_openai.call_args_list[0].kwargs
    models_kwargs = mock_openai.call_args_list[1].kwargs
    assert chat_kwargs['base_url'] == 'https://opencode.ai/inference/go/openai/v1'
    assert models_kwargs['base_url'] == 'https://opencode.ai/inference/go/v1'
    assert chat_kwargs['api_key'] == models_kwargs['api_key'] == 'oc_sk_test'
    # Client-specific UA; the session header is per-conversation, not global.
    assert chat_kwargs['default_headers']['User-Agent'].startswith('ai-provider-service/')
    assert 'x-opencode-session' not in chat_kwargs['default_headers']
    # get_models() must read the model list, not the chat base.
    client.get_models()
    mock_openai.return_value.models.list.assert_called_once()


@patch('providers.opencode.OpenAI')
def test_opencode_go_single_client_when_bases_match(mock_openai, monkeypatch):
    """A /zen/go/v1-style override (one base for both) must not build a second
    client — that route serves /models and /chat/completions under one base."""
    import providers.opencode as opencode_module
    monkeypatch.setattr(opencode_module.Config, 'OPENCODE_GO_BASE_URL',
                        'https://opencode.ai/zen/go/v1')
    monkeypatch.setattr(opencode_module.Config, 'OPENCODE_GO_MODELS_BASE_URL',
                        'https://opencode.ai/zen/go/v1')

    opencode_module.OpencodeGoClient({'api_key': 'oc_sk_test'})

    assert mock_openai.call_count == 1


@patch('providers.opencode.OpenAI')
def test_opencode_zen_builds_one_client(mock_openai):
    """Zen keeps a single client: its /models lives under the chat base."""
    from providers.opencode import OpencodeClient
    OpencodeClient({'api_key': 'sk-test'})
    assert mock_openai.call_count == 1


@patch('providers.opencode.OpenAI')
def test_opencode_go_falls_back_to_central_key(mock_openai, monkeypatch):
    import providers.opencode as opencode_module
    monkeypatch.setattr(opencode_module.Config, 'OPENCODE_GO_API_KEY', 'oc_sk_server')

    opencode_module.OpencodeGoClient({})

    _, kwargs = mock_openai.call_args
    assert kwargs['api_key'] == 'oc_sk_server'


@patch('providers.opencode.OpenAI')
def test_opencode_go_raises_without_any_key(mock_openai, monkeypatch):
    import providers.opencode as opencode_module
    monkeypatch.setattr(opencode_module.Config, 'OPENCODE_GO_API_KEY', '')
    with pytest.raises(ValueError, match='OPENCODE_GO_API_KEY'):
        opencode_module.OpencodeGoClient({})


@patch('providers.opencode.OpenAI')
def test_opencode_go_respects_custom_endpoint(mock_openai):
    from providers.opencode import OpencodeGoClient
    OpencodeGoClient({'api_key': 'oc_sk_test',
                      'api_endpoint': 'https://example.org/go/v1'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://example.org/go/v1'


def _fake_models_client(mock_openai, ids):
    mock_openai.return_value.models.list.return_value = MagicMock(
        data=[MagicMock(id=mid) for mid in ids])


@patch('providers.opencode.OpenAI')
def test_opencode_go_advertises_full_catalog(mock_openai):
    """No free-only gating: the Go plan covers paid models too, so the whole
    catalog is advertised even though the upstream list contains -free IDs."""
    from providers.opencode import OpencodeGoClient
    _fake_models_client(mock_openai, ['beam-free', 'kimi-k3', 'deepseek-v4.1-flash'])
    client = OpencodeGoClient({'api_key': 'oc_sk_test'})
    assert client.get_models() == ['beam-free', 'deepseek-v4.1-flash', 'kimi-k3']


@patch('providers.opencode.OpenAI')
def test_opencode_go_create_message_sends_session_header(mock_openai):
    from providers.opencode import OpencodeGoClient, _conversation_session_id
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='hi back'))]
    fake_response.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    create = mock_openai.return_value.chat.completions.create
    create.return_value = fake_response

    messages = [{'role': 'user', 'content': 'hi'}]
    OpencodeGoClient({'api_key': 'oc_sk_test'}).create_message('kimi-k3', messages, 50)

    # Mandatory upstream: a request without x-opencode-session fails (HTTP 500).
    assert create.call_args.kwargs['extra_headers']['x-opencode-session'] == \
        _conversation_session_id(messages)


@patch('providers.opencode.OpenAI')
def test_opencode_go_does_not_retry_free_variants_on_balance_error(mock_openai):
    """Zen retries a paid model with a free variant; Go must not — an exhausted
    Go plan surfaces as 402/429 and the dispatcher handles the fallback."""
    from openai import AuthenticationError

    import providers.opencode as opencode_module
    error = AuthenticationError(
        'insufficient balance', response=MagicMock(status_code=401),
        body=None)

    create = mock_openai.return_value.chat.completions.create
    create.side_effect = error

    client = opencode_module.OpencodeGoClient({'api_key': 'oc_sk_test'})
    with pytest.raises(AuthenticationError):
        client.create_message('kimi-k3', [{'role': 'user', 'content': 'hi'}], 50)

    # Exactly one attempt: no free-variant retries.
    assert create.call_count == 1


@patch('providers.opencode.OpenAI')
def test_opencode_go_health_uses_models_list(mock_openai):
    from providers.opencode import OpencodeGoClient
    client = OpencodeGoClient({'api_key': 'oc_sk_test'})
    mock_openai.return_value.models.list.return_value = MagicMock(data=[])
    assert client.health() is True
    mock_openai.return_value.models.list.side_effect = RuntimeError('401')
    assert client.health() is False
