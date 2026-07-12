"""Tests for ClineClient + factory registration.

ClineClient uses raw httpx (not the OpenAI SDK) because Cline wraps
responses in {"data": {"choices": [...], ...}, "success": true}.
"""

from unittest.mock import MagicMock, patch
import pytest
from providers import get_client, PROVIDER_REGISTRY


def test_cline_registered():
    assert 'cline' in PROVIDER_REGISTRY


def test_cline_requires_personal_api_key():
    meta = PROVIDER_REGISTRY['cline']
    assert meta['system'] is False
    assert meta['requires'] == ['api_key']
    assert 'api_endpoint' in meta['optional']
    assert meta['personal_api_key'] is True


def test_factory_returns_cline_client():
    client = get_client('cline', {'api_key': 'sk-test'})
    assert client.__class__.__name__ == 'ClineClient'


@patch('providers.cline.httpx.Client')
def test_uses_default_base_url(mock_httpx_client):
    from providers.cline import ClineClient
    c = ClineClient({'api_key': 'sk-test'})
    assert c._base_url == 'https://api.cline.bot/api/v1'
    assert c._api_key == 'sk-test'


@patch('providers.cline.httpx.Client')
def test_respects_custom_endpoint(mock_httpx_client):
    from providers.cline import ClineClient
    c = ClineClient({'api_key': 'sk-test', 'api_endpoint': 'https://example.org/v1'})
    assert c._base_url == 'https://example.org/v1'


def test_requires_api_key(monkeypatch):
    from providers.cline import ClineClient
    monkeypatch.setattr('providers.cline.Config.CLINE_API_KEY', '')
    with pytest.raises(ValueError):
        ClineClient({})


def test_uses_config_api_key_before_env(monkeypatch):
    from providers.cline import ClineClient
    monkeypatch.setattr('providers.cline.Config.CLINE_API_KEY', 'env-key')
    c = ClineClient({'api_key': 'cfg-key'})
    assert c._api_key == 'cfg-key'


@patch('providers.cline.httpx.Client')
def test_create_message_returns_claude_format(mock_httpx_client):
    from providers.cline import ClineClient
    fake_raw = {
        'data': {
            'choices': [{
                'message': {'content': 'hello from cline', 'role': 'assistant'},
                'finish_reason': 'stop',
            }],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 3},
        },
        'success': True,
    }
    mock_response = MagicMock()
    mock_response.json.return_value = fake_raw
    mock_response.raise_for_status.return_value = None
    mock_httpx_client_instance = MagicMock()
    mock_httpx_client_instance.post.return_value = mock_response
    # The context manager __enter__ returns the client instance
    mock_httpx_client.return_value.__enter__.return_value = mock_httpx_client_instance

    # Model name keeps its own slash (Cline uses `provider/model` ids).
    c = ClineClient({'api_key': 'sk-test'})
    out = c.create_message('anthropic/claude-sonnet-4-6', [{'role': 'user', 'content': 'hi'}], 50)

    # Verify the correct model was sent to Cline's API
    call_kwargs = mock_httpx_client_instance.post.call_args[1]
    assert call_kwargs['json']['model'] == 'anthropic/claude-sonnet-4-6'
    assert out == {
        'content': [{'text': 'hello from cline'}],
        'usage': {'input_tokens': 10, 'output_tokens': 3},
    }


def test_get_models_returns_sorted_ids_with_slashes():
    """get_models() falls back to pricing_overrides_cline.json (513 models)."""
    from providers.cline import ClineClient
    c = ClineClient({'api_key': 'sk-test'})
    models = c.get_models()
    assert len(models) > 100  # catalog has 513 models
    assert 'cline-pass/qwen3.7-plus' in models
    assert 'anthropic/claude-sonnet-4-6' in models
    assert 'openai/gpt-4o' in models


def test_get_models_falls_back_to_override():
    """get_models() always uses the override file (Cline has no /models endpoint)."""
    from providers.cline import ClineClient
    c = ClineClient({'api_key': 'sk-test'})
    models = c.get_models()
    assert len(models) > 100  # catalog has 513 models
    assert 'cline-pass/qwen3.7-plus' in models
    assert 'anthropic/claude-sonnet-4-6' in models
    assert 'openai/gpt-4o' in models


@patch('providers.cline.httpx.Client')
def test_health_returns_true_on_success(mock_httpx_client):
    from providers.cline import ClineClient
    fake_raw = {
        'data': {
            'choices': [{
                'message': {'content': 'pong', 'role': 'assistant'},
                'finish_reason': 'stop',
            }],
            'usage': {'prompt_tokens': 5, 'completion_tokens': 1},
        },
        'success': True,
    }
    mock_response = MagicMock()
    mock_response.json.return_value = fake_raw
    mock_response.status_code = 200
    mock_httpx_client_instance = MagicMock()
    mock_httpx_client_instance.post.return_value = mock_response
    mock_httpx_client.return_value.__enter__.return_value = mock_httpx_client_instance

    assert ClineClient({'api_key': 'sk-test'}).health() is True


@patch('providers.cline.httpx.Client')
def test_health_returns_false_on_5xx(mock_httpx_client):
    """Cline health() returns False on 5xx responses."""
    from providers.cline import ClineClient
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_httpx_client_instance = MagicMock()
    mock_httpx_client_instance.post.return_value = mock_response
    mock_httpx_client.return_value.__enter__.return_value = mock_httpx_client_instance

    assert ClineClient({'api_key': 'sk-test'}).health() is False


@patch('providers.cline.httpx.Client')
def test_health_returns_false_on_failure(mock_httpx_client):
    from providers.cline import ClineClient
    mock_httpx_client.side_effect = Exception('API down')
    assert ClineClient({'api_key': 'sk-test'}).health() is False


def test_model_name_with_slash_round_trips_through_openai_parser():
    """cline/anthropic/claude-sonnet-4-6 must split into provider=cline,
    model=anthropic/claude-sonnet-4-6 (model keeps its own slash)."""
    from api.openai_api import _parse_model
    provider_id, model_name, origin_app = _parse_model('cline/anthropic/claude-sonnet-4-6')
    assert provider_id == 'cline'
    assert model_name == 'anthropic/claude-sonnet-4-6'
    assert origin_app is None