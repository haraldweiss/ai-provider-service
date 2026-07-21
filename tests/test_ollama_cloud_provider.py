from unittest.mock import Mock

import pytest
import requests


def _response(data, status=200):
    response = Mock()
    response.status_code = status
    response.ok = status < 400
    response.json.return_value = data
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    return response


def test_requires_api_key():
    from providers.ollama_cloud import OllamaCloudClient
    with pytest.raises(ValueError, match='api_key'):
        OllamaCloudClient({})


def test_get_models_sends_bearer_header(monkeypatch):
    from providers.ollama_cloud import OllamaCloudClient
    get = Mock(return_value=_response({'models': [{'name': 'glm-4.6'}]}))
    monkeypatch.setattr('providers.ollama_cloud.requests.get', get)

    client = OllamaCloudClient({'api_key': 'secret'})

    assert client.get_models() == ['glm-4.6']
    assert get.call_args.args[0] == 'https://ollama.com/api/tags'
    assert get.call_args.kwargs['headers'] == {'Authorization': 'Bearer secret'}


def test_create_message_maps_native_response(monkeypatch):
    from providers.ollama_cloud import OllamaCloudClient
    post = Mock(return_value=_response({
        'message': {'content': 'hello'},
        'prompt_eval_count': 7,
        'eval_count': 3,
    }))
    monkeypatch.setattr('providers.ollama_cloud.requests.post', post)

    result = OllamaCloudClient({'api_key': 'secret'}).create_message(
        'glm-4.6', [{'role': 'user', 'content': 'hi'}], 123,
    )

    assert result == {
        'content': [{'text': 'hello'}],
        'usage': {'input_tokens': 7, 'output_tokens': 3},
    }
    payload = post.call_args.kwargs['json']
    assert payload['stream'] is False
    assert payload['options']['num_predict'] == 123


def test_http_error_is_sanitized(monkeypatch):
    from providers.ollama_cloud import OllamaCloudClient
    monkeypatch.setattr(
        'providers.ollama_cloud.requests.get',
        Mock(return_value=_response({'error': 'secret leaked'}, status=401)),
    )

    with pytest.raises(RuntimeError) as caught:
        OllamaCloudClient({'api_key': 'secret'}).get_models()
    assert caught.value.status_code == 401
    assert '401' in str(caught.value)
    assert 'secret' not in str(caught.value)


def test_factory_returns_cloud_client():
    from providers import get_client
    from providers.ollama_cloud import OllamaCloudClient
    assert isinstance(get_client('ollama_cloud', {'api_key': 'x'}), OllamaCloudClient)
