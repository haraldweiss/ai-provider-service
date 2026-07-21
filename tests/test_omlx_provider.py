from unittest.mock import patch

from providers.omlx import OmlxClient


def test_omlx_uses_extended_chat_timeout():
    assert OmlxClient.timeout == 180


@patch('providers.omlx.requests.get')
def test_get_models_uses_v1_models_and_bearer_key(mock_get):
    mock_get.return_value.json.return_value = {'data': [{'id': 'devstral'}]}
    mock_get.return_value.raise_for_status.return_value = None

    client = OmlxClient({
        'api_endpoint': 'http://omlx.example:11442/v1',
        'api_key': 'test-key',
    })

    assert client.get_models() == ['devstral']
    mock_get.assert_called_once_with(
        'http://omlx.example:11442/v1/models',
        headers={'Authorization': 'Bearer test-key'},
        timeout=5,
    )


@patch('providers.omlx.requests.post')
def test_create_message_forwards_tools_and_maps_openai_response(mock_post):
    mock_post.return_value.json.return_value = {
        'choices': [{'message': {'content': 'done'}}],
        'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
    }
    mock_post.return_value.raise_for_status.return_value = None

    result = OmlxClient({'api_key': 'test-key'}).create_message(
        'devstral', [{'role': 'user', 'content': 'hi'}], 99,
        tools=[{'type': 'function', 'function': {'name': 'read'}}],
    )

    assert result == {
        'content': [{'text': 'done'}],
        'usage': {'input_tokens': 3, 'output_tokens': 2},
    }
    assert mock_post.call_args.kwargs['json']['tools'][0]['function']['name'] == 'read'


@patch('providers.omlx.requests.get', side_effect=OSError('offline'))
def test_health_returns_false_when_omlx_is_unreachable(_mock_get):
    assert OmlxClient({'api_key': 'test-key'}).health() is False
