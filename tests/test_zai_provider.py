# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ZaiClient (z.ai / GLM, OpenAI-compatible) + factory registration."""

from unittest.mock import MagicMock, patch
import pytest
from providers import get_client, PROVIDER_REGISTRY


def test_zai_registered():
    assert 'zai' in PROVIDER_REGISTRY


def test_zai_is_system_provider_with_optional_api_key():
    """z.ai ist System-Provider: der zentrale ZAI_API_KEY ist nur für den
    Owner (Allowlist) freigeschaltet. Andere User konfigurieren ihren eigenen
    Key — daher ist api_key 'optional', nicht 'requires'."""
    meta = PROVIDER_REGISTRY['zai']
    assert meta['system'] is True
    assert 'api_key' not in meta['requires']
    assert 'api_key' in meta['optional']


def test_factory_returns_zai_client():
    client = get_client('zai', {'api_key': 'sk-test'})
    assert client.__class__.__name__ == 'ZaiClient'


def test_zai_raises_without_any_api_key(monkeypatch):
    import providers.zai as z
    monkeypatch.setattr(z.Config, 'ZAI_API_KEY', '')
    with pytest.raises(ValueError, match='api_key'):
        get_client('zai', {})


@patch('providers.zai.OpenAI')
def test_zai_uses_default_base_url(mock_openai):
    from providers.zai import ZaiClient
    ZaiClient({'api_key': 'sk-test'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://api.z.ai/api/paas/v4'
    assert kwargs['api_key'] == 'sk-test'


@patch('providers.zai.OpenAI')
def test_zai_respects_custom_endpoint(mock_openai):
    from providers.zai import ZaiClient
    ZaiClient({'api_key': 'sk-test', 'api_endpoint': 'https://example.org/v1'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://example.org/v1'


@patch('providers.zai.OpenAI')
def test_zai_falls_back_to_config_key(mock_openai, monkeypatch):
    import providers.zai as z
    monkeypatch.setattr(z.Config, 'ZAI_API_KEY', 'sys-key')
    z.ZaiClient({})
    _, kwargs = mock_openai.call_args
    assert kwargs['api_key'] == 'sys-key'


@patch('providers.zai.OpenAI')
def test_zai_create_message_returns_claude_format(mock_openai):
    from providers.zai import ZaiClient
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(content='hallo'))]
    fake_response.usage = MagicMock(prompt_tokens=11, completion_tokens=4)
    mock_openai.return_value.chat.completions.create.return_value = fake_response

    c = ZaiClient({'api_key': 'sk-test'})
    out = c.create_message('glm-4.6', [{'role': 'user', 'content': 'hi'}], 50)

    assert out['content'] == [{'text': 'hallo'}]
    assert out['usage'] == {'input_tokens': 11, 'output_tokens': 4}
    _, kwargs = mock_openai.return_value.chat.completions.create.call_args
    assert kwargs['model'] == 'glm-4.6'
    assert kwargs['max_tokens'] == 50


@patch('providers.zai.OpenAI')
def test_zai_get_models_filters_glm(mock_openai):
    from providers.zai import ZaiClient
    mock_openai.return_value.models.list.return_value.data = [
        MagicMock(id='glm-4.6'), MagicMock(id='glm-4.5-air'),
        MagicMock(id='whisper-1'),
    ]
    c = ZaiClient({'api_key': 'sk-test'})
    models = c.get_models()
    assert 'glm-4.6' in models
    assert 'glm-4.5-air' in models
    assert 'whisper-1' not in models


@patch('providers.zai.OpenAI')
def test_zai_health_true_when_models_list_ok(mock_openai):
    from providers.zai import ZaiClient
    c = ZaiClient({'api_key': 'sk-test'})
    assert c.health() is True


@patch('providers.zai.OpenAI')
def test_zai_health_false_on_exception(mock_openai):
    from providers.zai import ZaiClient
    mock_openai.return_value.models.list.side_effect = RuntimeError('boom')
    c = ZaiClient({'api_key': 'sk-test'})
    assert c.health() is False
