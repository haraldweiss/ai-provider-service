"""Tests for ClineClient + factory registration."""

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


@patch('providers.cline.OpenAI')
def test_uses_default_base_url(mock_openai):
    from providers.cline import ClineClient
    ClineClient({'api_key': 'sk-test'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://api.cline.bot/api/v1'
    assert kwargs['api_key'] == 'sk-test'


@patch('providers.cline.OpenAI')
def test_respects_custom_endpoint(mock_openai):
    from providers.cline import ClineClient
    ClineClient({'api_key': 'sk-test', 'api_endpoint': 'https://example.org/v1'})
    _, kwargs = mock_openai.call_args
    assert kwargs['base_url'] == 'https://example.org/v1'


def test_requires_api_key():
    from providers.cline import ClineClient
    with pytest.raises(ValueError):
        ClineClient({})


@patch('providers.cline.OpenAI')
def test_uses_config_api_key_before_env(mock_openai, monkeypatch):
    from providers.cline import ClineClient
    monkeypatch.setattr('providers.cline.Config.CLINE_API_KEY', 'env-key')
    ClineClient({'api_key': 'cfg-key'})
    _, kwargs = mock_openai.call_args
    assert kwargs['api_key'] == 'cfg-key'


@patch('providers.cline.OpenAI')
@patch('providers.cline.httpx.Client')
def test_create_message_returns_claude_format(mock_httpx_client, mock_openai):
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


@patch('providers.cline.OpenAI')
def test_get_models_returns_sorted_ids_with_slashes(mock_openai):
    from providers.cline import ClineClient
    mock_client_instance = MagicMock()
    m1 = MagicMock(); m1.id = 'openai/gpt-5'
    m2 = MagicMock(); m2.id = 'anthropic/claude-sonnet-4-6'
    mock_client_instance.models.list.return_value.data = [m1, m2]
    mock_openai.return_value = mock_client_instance

    c = ClineClient({'api_key': 'sk-test'})
    assert c.get_models() == ['anthropic/claude-sonnet-4-6', 'openai/gpt-5']


@patch('providers.cline.OpenAI')
def test_get_models_falls_back_to_override_on_404(mock_openai):
    """When Cline's API returns 404 (NotFoundError), fall back to the
    pricing_overrides_cline.json to extract model IDs."""
    from openai import NotFoundError
    from providers.cline import ClineClient
    mock_openai.return_value.models.list.side_effect = NotFoundError(
        '404 Not Found',
        response=MagicMock(status_code=404),
        body={'error': 'Not Found'},
    )
    c = ClineClient({'api_key': 'sk-test'})
    models = c.get_models()
    assert len(models) > 100  # catalog has 513 models
    assert 'cline-pass/qwen3.7-plus' in models
    assert 'anthropic/claude-sonnet-4-6' in models
    assert 'openai/gpt-4o' in models


@patch('providers.cline.OpenAI')
def test_health_returns_true_on_success(mock_openai):
    from providers.cline import ClineClient
    mock_openai.return_value.models.list.return_value = MagicMock()
    assert ClineClient({'api_key': 'sk-test'}).health() is True


@patch('providers.cline.OpenAI')
def test_health_returns_true_on_404(mock_openai):
    """Cline's API returns 404 for /models — that's not a connectivity
    problem, the server is alive."""
    from openai import NotFoundError
    from providers.cline import ClineClient
    mock_openai.return_value.models.list.side_effect = NotFoundError(
        '404 Not Found',
        response=MagicMock(status_code=404),
        body={'error': 'Not Found'},
    )
    assert ClineClient({'api_key': 'sk-test'}).health() is True


@patch('providers.cline.OpenAI')
def test_health_returns_false_on_failure(mock_openai):
    from providers.cline import ClineClient
    mock_openai.return_value.models.list.side_effect = Exception('API down')
    assert ClineClient({'api_key': 'sk-test'}).health() is False


def test_model_name_with_slash_round_trips_through_openai_parser():
    """cline/anthropic/claude-sonnet-4-6 must split into provider=cline,
    model=anthropic/claude-sonnet-4-6 (model keeps its own slash)."""
    from api.openai_api import _parse_model
    provider_id, model_name, origin_app = _parse_model('cline/anthropic/claude-sonnet-4-6')
    assert provider_id == 'cline'
    assert model_name == 'anthropic/claude-sonnet-4-6'
    assert origin_app is None
