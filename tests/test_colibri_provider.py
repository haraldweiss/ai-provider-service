"""Tests for the authenticated Colibri REST/SSE provider adapter."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests

from config import Config


def fake_sse_response(frames: list[dict | str]) -> Mock:
    response = Mock()
    response.iter_lines.return_value = [
        f"data: {json.dumps(frame)}" if isinstance(frame, dict) else f"data: {frame}"
        for frame in frames
    ]
    return response


@pytest.fixture(autouse=True)
def colibri_config(monkeypatch):
    monkeypatch.setattr(Config, 'COLIBRI_ENABLED', True)
    monkeypatch.setattr(Config, 'COLIBRI_API_KEY', 'colibri-test-key')
    monkeypatch.setattr(Config, 'COLIBRI_BASE_URL', 'http://colibri.test:18000')
    monkeypatch.setattr(Config, 'COLIBRI_MODEL_ID', 'glm-5.2-colibri')
    monkeypatch.setattr(Config, 'COLIBRI_CONNECT_TIMEOUT_SEC', 10)
    monkeypatch.setattr(Config, 'COLIBRI_STREAM_READ_TIMEOUT_SEC', 180)


def test_stream_forwards_tools_and_emits_multiple_frames(monkeypatch):
    from providers.colibri import ColibriClient

    post = Mock(return_value=fake_sse_response([
        {'choices': [{'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]},
        {'choices': [{'delta': {'content': 'Hallo'}, 'finish_reason': None}]},
        {'choices': [{'delta': {}, 'finish_reason': 'stop'}],
         'usage': {'prompt_tokens': 1, 'completion_tokens': 1}},
    ]))
    monkeypatch.setattr('providers.colibri.requests.post', post)

    stream = ColibriClient({}).open_stream(
        'glm-5.2-colibri', [{'role': 'user', 'content': 'Hi'}], 16,
        tools=[{'type': 'function', 'function': {'name': 'read_file'}}],
        tool_choice='auto',
    )

    assert len(list(stream.events)) == 3
    assert post.call_args.kwargs['json']['stream'] is True
    assert post.call_args.kwargs['json']['tools'][0]['function']['name'] == 'read_file'
    assert post.call_args.kwargs['json']['tool_choice'] == 'auto'
    assert stream.result == {
        'content': [{'text': 'Hallo'}],
        'tool_calls': [],
        'usage': {'input_tokens': 1, 'output_tokens': 1},
        'stop_reason': 'stop',
    }
    stream.close()


def test_stream_rejects_an_unoffered_tool_before_yield(monkeypatch):
    from providers.colibri import ColibriClient, ColibriProtocolError

    response = fake_sse_response([
        {'choices': [{'delta': {'tool_calls': [
            {'function': {'name': 'erase_all', 'arguments': '{}'}},
        ]}, 'finish_reason': None}]},
    ])
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))

    stream = ColibriClient({}).open_stream(
        'glm-5.2-colibri', [], 16,
        tools=[{'type': 'function', 'function': {'name': 'read_file'}}],
        tool_choice='auto',
    )

    with pytest.raises(ColibriProtocolError, match='unoffered tool'):
        next(stream.events)
    response.close.assert_called_once()
    stream.close()


def test_stream_rejects_clean_eof_without_terminal_marker(monkeypatch):
    from providers.colibri import ColibriClient, ColibriProtocolError

    response = fake_sse_response([
        {'choices': [{'delta': {'content': 'partial'}, 'finish_reason': None}]},
    ])
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))

    stream = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)

    with pytest.raises(ColibriProtocolError, match='terminal marker'):
        list(stream.events)
    response.close.assert_called_once()


def test_client_requires_enabled_server_configuration(monkeypatch):
    from providers.colibri import ColibriClient

    monkeypatch.setattr(Config, 'COLIBRI_ENABLED', False)
    with pytest.raises(ValueError, match='Colibri is disabled or has no API key'):
        ColibriClient({'api_key': 'personal-key'})

    monkeypatch.setattr(Config, 'COLIBRI_ENABLED', True)
    monkeypatch.setattr(Config, 'COLIBRI_API_KEY', '')
    with pytest.raises(ValueError, match='Colibri is disabled or has no API key'):
        ColibriClient({'api_key': 'personal-key'})


def test_models_and_health_use_bearer_auth_without_exposing_key(monkeypatch):
    from providers.colibri import ColibriClient

    get = Mock(side_effect=[
        Mock(json=Mock(return_value={'data': [{'id': 'other'}]})),
        Mock(json=Mock(return_value={'status': 'ok'})),
    ])
    monkeypatch.setattr('providers.colibri.requests.get', get)
    client = ColibriClient({'api_key': 'must-be-ignored'})

    assert client.get_models() == ['glm-5.2-colibri']
    assert client.health() is True
    assert get.call_args_list[0].args[0] == 'http://colibri.test:18000/v1/models'
    assert get.call_args_list[0].kwargs == {
        'headers': {'Authorization': 'Bearer colibri-test-key'}, 'timeout': 5,
    }
    assert get.call_args_list[1].args[0] == 'http://colibri.test:18000/health'
    assert get.call_args_list[1].kwargs == {
        'headers': {'Authorization': 'Bearer colibri-test-key'}, 'timeout': 5,
    }


def test_health_returns_false_for_unhealthy_or_invalid_response(monkeypatch):
    from providers.colibri import ColibriClient

    get = Mock(return_value=Mock(json=Mock(return_value={'status': 'degraded'})))
    monkeypatch.setattr('providers.colibri.requests.get', get)

    assert ColibriClient({}).health() is False


def test_open_stream_checks_http_status_before_returning_handle(monkeypatch):
    from providers.colibri import ColibriClient

    response = fake_sse_response([])
    response.raise_for_status.side_effect = requests.HTTPError('bad status')
    post = Mock(return_value=response)
    monkeypatch.setattr('providers.colibri.requests.post', post)

    with pytest.raises(requests.HTTPError, match='bad status'):
        ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)

    response.close.assert_called_once()
    assert post.call_args.kwargs['stream'] is True
    assert post.call_args.kwargs['timeout'] == (10, 180)


def test_open_stream_maps_upstream_429_to_bounded_admission_error(monkeypatch):
    from providers.colibri import ColibriClient, ColibriOverloaded

    response = fake_sse_response([])
    response.status_code = 429
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))

    with pytest.raises(ColibriOverloaded, match='colibri_queue_full'):
        ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)

    response.close.assert_called_once()


def test_open_stream_maps_upstream_queue_timeout_code(monkeypatch):
    from providers.colibri import ColibriClient, ColibriOverloaded

    response = fake_sse_response([])
    response.status_code = 429
    response.json.return_value = {
        'error': {'code': 'queue_timeout', 'message': 'Timed out waiting.'},
    }
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))

    with pytest.raises(ColibriOverloaded, match='colibri_queue_timeout'):
        ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)

    response.close.assert_called_once()


def test_open_stream_preserves_transport_timeout_without_protocol_signal(monkeypatch):
    from providers.colibri import ColibriClient

    post = Mock(side_effect=requests.ReadTimeout('upstream read timed out'))
    monkeypatch.setattr('providers.colibri.requests.post', post)

    with pytest.raises(requests.ReadTimeout, match='upstream read timed out'):
        ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)


def test_stream_accepts_only_data_frames_and_rejects_invalid_sse(monkeypatch):
    from providers.colibri import ColibriClient, ColibriProtocolError

    response = fake_sse_response([])
    response.iter_lines.return_value = [
        b': ping', b'', b'event: message',
        b'data: {"choices": []}',
    ]
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))
    stream = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)

    with pytest.raises(ColibriProtocolError, match='choices'):
        next(stream.events)
    response.close.assert_called_once()


def test_stream_rejects_invalid_json_data_frame(monkeypatch):
    from providers.colibri import ColibriClient, ColibriProtocolError

    response = fake_sse_response([])
    response.iter_lines.return_value = [b'data: not-json']
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))
    stream = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)

    with pytest.raises(ColibriProtocolError, match='invalid SSE JSON'):
        next(stream.events)
    response.close.assert_called_once()


def test_stream_close_is_idempotent_and_does_not_block_next_upstream_open(monkeypatch):
    from providers.colibri import ColibriClient

    first_response = fake_sse_response([])
    second_response = fake_sse_response([])
    post = Mock(side_effect=[first_response, second_response])
    monkeypatch.setattr('providers.colibri.requests.post', post)

    first = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)
    first.close()
    first.close()
    second = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)

    assert first_response.close.call_count == 1
    second.close()


def test_gateway_opens_requests_directly_for_service_admission(monkeypatch):
    from providers import colibri
    from providers.colibri import ColibriClient

    assert not hasattr(colibri, '_active_request')
    assert not hasattr(colibri, '_waiter_count')
    assert not hasattr(colibri, '_waiter_lock')
    responses = [fake_sse_response([]) for _ in range(2)]
    post = Mock(side_effect=responses)
    monkeypatch.setattr('providers.colibri.requests.post', post)

    first = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)
    try:
        second = ColibriClient({}).open_stream('glm-5.2-colibri', [], 16)
    finally:
        first.close()

    assert post.call_count == 2
    second.close()


@pytest.mark.parametrize(
    ('finish_reason', 'stop_reason'), [('length', 'length'), (None, 'stop')],
)
def test_create_message_maps_openai_response_and_releases_admission(
    monkeypatch, finish_reason, stop_reason,
):
    from providers.colibri import ColibriClient

    response = Mock()
    response.json.return_value = {
        'choices': [{'message': {'content': 'Hello'}, 'finish_reason': finish_reason}],
        'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
    }
    post = Mock(return_value=response)
    monkeypatch.setattr('providers.colibri.requests.post', post)

    result = ColibriClient({}).create_message(
        'ignored-model', [{'role': 'user', 'content': 'Hi'}], 16,
        tools=[{'type': 'function', 'function': {'name': 'read_file'}}],
        tool_choice='required',
    )

    assert result == {
        'content': [{'text': 'Hello'}],
        'tool_calls': [],
        'usage': {'input_tokens': 2, 'output_tokens': 3},
        'stop_reason': stop_reason,
    }
    assert post.call_args.kwargs['json'] == {
        'model': 'glm-5.2-colibri',
        'messages': [{'role': 'user', 'content': 'Hi'}],
        'max_completion_tokens': 16,
        'stream': False,
        'tools': [{'type': 'function', 'function': {'name': 'read_file'}}],
        'tool_choice': 'required',
    }
    response.close.assert_called_once()


def test_create_message_rejects_unoffered_tool_call(monkeypatch):
    from providers.colibri import ColibriClient, ColibriProtocolError

    response = Mock()
    response.json.return_value = {
        'choices': [{'message': {'content': None, 'tool_calls': [{
            'id': 'call_1',
            'function': {'name': 'erase_all', 'arguments': '{}'},
        }]}, 'finish_reason': 'tool_calls'}],
    }
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))

    with pytest.raises(ColibriProtocolError, match='unoffered tool'):
        ColibriClient({}).create_message(
            'glm-5.2-colibri', [], 16,
            tools=[{'type': 'function', 'function': {'name': 'read_file'}}],
        )
    response.close.assert_called_once()


@pytest.mark.parametrize('tool_call', [
    {'function': {'name': '', 'arguments': '{}'}},
    {'function': 'not-an-object'},
    'not-an-object',
])
def test_create_message_rejects_malformed_tool_call(monkeypatch, tool_call):
    from providers.colibri import ColibriClient, ColibriProtocolError

    response = Mock()
    response.json.return_value = {
        'choices': [{'message': {'tool_calls': [tool_call]}, 'finish_reason': 'tool_calls'}],
    }
    monkeypatch.setattr('providers.colibri.requests.post', Mock(return_value=response))

    with pytest.raises(ColibriProtocolError, match='invalid tool call'):
        ColibriClient({}).create_message(
            'glm-5.2-colibri', [], 16,
            tools=[{'type': 'function', 'function': {'name': 'read_file'}}],
        )
    response.close.assert_called_once()
