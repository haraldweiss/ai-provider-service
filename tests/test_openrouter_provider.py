"""Tests for OpenRouterClient + factory registration."""

import json
import time
from unittest.mock import MagicMock, patch
from providers import get_client, PROVIDER_REGISTRY


def test_openrouter_registered():
    assert 'openrouter' in PROVIDER_REGISTRY


def test_openrouter_is_system_provider_with_optional_api_key():
    meta = PROVIDER_REGISTRY['openrouter']
    assert meta['system'] is True
    assert 'api_key' not in meta['requires']
    assert 'api_key' in meta['optional']


def test_factory_returns_openrouter_client():
    client = get_client('openrouter', {'api_key': 'sk-test'})
    assert client.__class__.__name__ == 'OpenRouterClient'


def test_factory_works_without_api_key():
    client = get_client('openrouter', {})
    assert client.__class__.__name__ == 'OpenRouterClient'


@patch('providers.openrouter.OpenAI')
def test_uses_default_base_url(mock_openai):
    from providers.openrouter import OpenRouterClient
    OpenRouterClient({'api_key': 'sk-test'})
    args, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://openrouter.ai/api/v1'
    assert kwargs['api_key'] == 'sk-test'


@patch('providers.openrouter.OpenAI')
def test_respects_custom_endpoint(mock_openai):
    from providers.openrouter import OpenRouterClient
    OpenRouterClient({'api_key': 'sk-test', 'api_endpoint': 'https://example.org/v1'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://example.org/v1'


@patch('providers.openrouter.OpenAI')
def test_create_message_returns_claude_format(mock_openai):
    from providers.openrouter import OpenRouterClient
    fake_response = MagicMock()
    fake_choice = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = 'hello from openrouter'
    fake_choice.message = fake_msg
    fake_response.choices = [fake_choice]
    fake_response.usage = MagicMock(prompt_tokens=10, completion_tokens=3)
    mock_openai.return_value.chat.completions.create.return_value = fake_response

    c = OpenRouterClient({'api_key': 'sk-test'})
    out = c.create_message('model-x', [{'role': 'user', 'content': 'hi'}], 50)

    assert out == {
        'content': [{'text': 'hello from openrouter'}],
        'usage': {'input_tokens': 10, 'output_tokens': 3},
    }


@patch('providers.openrouter.OpenAI')
def test_free_only_filters_models(mock_openai):
    from providers.openrouter import OpenRouterClient

    mock_client_instance = MagicMock()
    model_a = MagicMock()
    model_a.id = 'free-model-a'
    model_b = MagicMock()
    model_b.id = 'paid-model-b'

    mock_client_instance.models.list.return_value.data = [model_a, model_b]
    mock_openai.return_value = mock_client_instance

    with patch('providers.openrouter._get_cached_free_models', return_value=['free-model-a']):
        c = OpenRouterClient({'_free_only': True, 'api_key': 'sk-test'})
        models = c.get_models()

    assert models == ['free-model-a']


def test_expired_free_model_cache_falls_back_when_refresh_fails(tmp_path, monkeypatch):
    from providers import openrouter

    cache_file = tmp_path / 'openrouter_free_models.json'
    cache_file.write_text(json.dumps({
        'ts': time.time() - openrouter._FREE_CACHE_TTL - 60,
        'models': ['stale-free-model'],
    }))
    monkeypatch.setattr(openrouter, '_FREE_CACHE_FILE', str(cache_file))

    client = MagicMock()
    client.models.list.side_effect = Exception('API down')

    assert openrouter._get_cached_free_models(client) == ['stale-free-model']


@patch('providers.openrouter.OpenAI')
def test_try_refresh_free_models_uses_default_openrouter_config(mock_openai):
    from providers.openrouter import OpenRouterClient

    mock_client_instance = MagicMock()
    model = MagicMock()
    model.id = 'free-model-a'
    model.pricing = {'prompt': '0', 'completion': '0'}
    mock_client_instance.models.list.return_value.data = [model]
    mock_openai.return_value = mock_client_instance

    with patch('providers.openrouter.Config.OPENROUTER_API_KEY', 'sk-openrouter'):
        free = OpenRouterClient.try_refresh_free_models()

    _, kwargs = mock_openai.call_args
    assert kwargs['api_key'] == 'sk-openrouter'
    assert kwargs['base_url'] == 'https://openrouter.ai/api/v1'
    assert free == ['free-model-a']


@patch('providers.openrouter.OpenAI')
def test_health_returns_true_on_success(mock_openai):
    from providers.openrouter import OpenRouterClient
    mock_openai.return_value.models.list.return_value = MagicMock()
    c = OpenRouterClient({'api_key': 'sk-test'})
    assert c.health() is True


@patch('providers.openrouter.OpenAI')
def test_health_returns_false_on_failure(mock_openai):
    from providers.openrouter import OpenRouterClient
    mock_openai.return_value.models.list.side_effect = Exception('API down')
    c = OpenRouterClient({'api_key': 'sk-test'})
    assert c.health() is False
