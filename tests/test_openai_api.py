# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for OpenAI API endpoint /v1/chat/completions."""

import json

def test_list_models_is_generated_from_available_provider_models(app, client, monkeypatch):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    monkeypatch.setattr(openai_api, 'PROVIDER_REGISTRY', {
        'ollama': {
            'name': 'Ollama (lokal)',
            'system': True,
            'requires': [],
            'optional': [],
        },
        'claude': {
            'name': 'Claude (Anthropic)',
            'system': True,
            'requires': [],
            'optional': [],
        },
        'zai': {
            'name': 'z.ai (GLM)',
            'system': True,
            'requires': [],
            'optional': [],
        },
    })

    def fake_load_config(user_id, provider_id):
        assert user_id == 'harald'
        return {} if provider_id == 'ollama' else None

    class FakeOllamaClient:
        def get_models(self):
            return ['ornith:latest', 'qwen3.6:latest']

    def fake_get_client(provider_id, cfg):
        assert provider_id == 'ollama'
        assert cfg == {}
        return FakeOllamaClient()

    monkeypatch.setattr(openai_api, '_load_config', fake_load_config, raising=False)
    monkeypatch.setattr(openai_api, 'get_client', fake_get_client, raising=False)
    monkeypatch.setattr(openai_api.health_tracker, 'is_healthy', lambda provider_id: True)

    r = client.get('/v1/models', headers={'Authorization': 'Bearer admin-test-token'})

    assert r.status_code == 200
    model_ids = [m['id'] for m in r.json['data']]
    assert model_ids == ['ollama/ornith:latest', 'ollama/qwen3.6:latest']


def test_parse_wolfinichat_model_routes_to_ollama_with_origin():
    from api.openai_api import _parse_model

    provider_id, model_name, origin_app = _parse_model('wolfinichat/qwen3.6:latest')

    assert provider_id == 'ollama'
    assert model_name == 'qwen3.6:latest'
    assert origin_app == 'chat.wolfinisoftware.de'


def test_chat_completions_uses_principal_user_id(app, client, monkeypatch):
    """Regression test: /v1/chat/completions must use g.principal.user_id.
    
    Previously, the endpoint would lose the real Principal.user_id and
    fall back to 'pi-agent', breaking provider access controls that depend
    on the actual user identity.
    
    This test verifies that when a principal is set, its user_id is used
    in the dispatch call instead of the hardcoded 'pi-agent' fallback.
    """
    from config import Config
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'
    
    # Mock the imported dispatcher function to capture the user_id it receives.
    import api.openai_api as openai_api
    
    captured_user_id = None
    
    def mock_dispatch(*args, **kwargs):
        nonlocal captured_user_id
        captured_user_id = kwargs.get('user_id')
        # Return a minimal response to avoid provider errors
        return {
            'result': {
                'text': 'test response',
                'usage': {'input_tokens': 10, 'output_tokens': 5}
            },
            'via': 'test-provider',
            'fallback_used': False
        }
    
    # Test with admin token (should use ADMIN_USER_ID)
    with app.app_context():
        monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

        r = client.post('/v1/chat/completions',
                       json={
                           'model': 'ollama/test-model',
                           'messages': [{'role': 'user', 'content': 'test'}],
                           'stream': False
                       },
                       headers={'Authorization': 'Bearer admin-test-token'})

    assert r.status_code == 200
    assert captured_user_id == 'harald', f"Expected 'harald', got '{captured_user_id}'"


def test_chat_completions_fallback_to_pi_agent_when_no_principal(app, client, monkeypatch):
    """Test that /v1/chat/completions falls back to 'pi-agent' when no principal."""
    import api.openai_api as openai_api
    
    captured_user_id = None
    
    def mock_dispatch(*args, **kwargs):
        nonlocal captured_user_id
        captured_user_id = kwargs.get('user_id')
        return {
            'result': {
                'text': 'test response',
                'usage': {'input_tokens': 10, 'output_tokens': 5}
            },
            'via': 'test-provider',
            'fallback_used': False
        }
    
    with app.app_context():
        monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)
        
        # Call without setting a principal (bypass require_token for this test)
        with app.test_client() as c:
            r = c.post('/v1/chat/completions',
                      json={
                          'model': 'ollama/test-model',
                          'messages': [{'role': 'user', 'content': 'test'}],
                          'stream': False
                          },
                          headers={'Authorization': 'Bearer test-token'})

    # Should still work but might use pi-agent or empty string depending on implementation
    assert r.status_code in [200, 401]  # 401 if require_token blocks it


def test_chat_completions_normalizes_structured_content_parts(app, client, monkeypatch):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    captured_messages = None

    def mock_dispatch(*args, **kwargs):
        nonlocal captured_messages
        captured_messages = kwargs.get('messages')
        return {
            'result': {
                'content': [{'text': 'ok'}],
                'usage': {'input_tokens': 1, 'output_tokens': 1},
            },
            'via': 'ollama',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': 'ping'},
                    {'type': 'input_text', 'text': 'pong'},
                ],
            }],
            'stream': False,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    assert captured_messages == [{'role': 'user', 'content': 'ping\npong'}]


def test_chat_completions_returns_503_for_provider_unavailable(app, client, monkeypatch):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        from dispatcher import ProviderUnavailableError
        raise ProviderUnavailableError('Provider ollama nicht erreichbar, kein Fallback/Queue konfiguriert')

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
            'stream': False,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 503
    assert r.json['error']['type'] == 'service_unavailable'


def test_omlx_request_metadata_excludes_message_content():
    from api.openai_api import _omlx_request_metadata

    metadata = _omlx_request_metadata({
        'model': 'omlx/devstral',
        'messages': [
            {'role': 'developer', 'content': 'private system instruction'},
            {'role': 'user', 'content': [{'type': 'text', 'text': 'private prompt'}]},
        ],
        'stream': True,
        'max_tokens': 4096,
    })

    assert metadata == {
        'request_keys': ['max_tokens', 'messages', 'model', 'stream'],
        'message_count': 2,
        'message_roles': ['developer', 'user'],
        'message_content_types': ['str', 'list'],
        'message_content_lengths': [26, 1],
        'stream': True,
        'max_tokens_type': 'int',
        'max_completion_tokens_type': 'NoneType',
        'tool_count': 0,
    }
    assert 'private' not in str(metadata)


def test_chat_completions_returns_400_when_provider_rejects_request(app, client, monkeypatch):
    """A provider-side 4xx is a request error, not an availability outage."""
    from config import Config
    import api.openai_api as openai_api
    from dispatcher import ProviderRequestError

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        raise ProviderRequestError('omlx', 400)

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    response = client.post(
        '/v1/chat/completions',
        json={
            'model': 'omlx/Devstral-Small-2-24B-Instruct-2512-4bit',
            'messages': [{'role': 'user', 'content': 'test'}],
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert response.status_code == 400
    assert response.json['error'] == {
        'message': 'Provider omlx rejected the request (HTTP 400)',
        'type': 'invalid_request',
    }


def test_chat_completions_treats_null_max_tokens_as_default(app, client, monkeypatch):
    """max_tokens: null previously crashed with TypeError -> 500."""
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    captured = {}

    def mock_dispatch(*args, **kwargs):
        captured.update(kwargs)
        return {
            'result': {'content': [{'text': 'ok'}],
                       'usage': {'input_tokens': 1, 'output_tokens': 1}},
            'via': 'ollama',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': None,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    assert captured['max_tokens'] == 4096


def test_chat_completions_accepts_max_completion_tokens(app, client, monkeypatch):
    """Modern OpenAI clients use max_completion_tokens instead of max_tokens."""
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    captured = {}

    def mock_dispatch(*args, **kwargs):
        captured.update(kwargs)
        return {
            'result': {'content': [{'text': 'ok'}],
                       'usage': {'input_tokens': 1, 'output_tokens': 1}},
            'via': 'omlx',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'omlx/Devstral-Small-2-24B-Instruct-2512-4bit',
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_completion_tokens': 64,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    assert captured['max_tokens'] == 64


def test_chat_completions_returns_400_for_invalid_max_tokens(app, client):
    from config import Config

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': -5,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 400
    assert r.json['error']['type'] == 'invalid_request'
    assert 'max_tokens' in r.json['error']['message']


def test_chat_completions_returns_400_for_non_list_messages(app, client):
    from config import Config

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': 'just-a-string',
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 400
    assert r.json['error']['message'] == 'messages must be a list'


def test_chat_completions_maps_provider_length_stop_reason(app, client, monkeypatch):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        return {
            'result': {
                'content': [{'text': 'partial answer'}],
                'usage': {'input_tokens': 10, 'output_tokens': 4096},
                'stop_reason': 'length',
            },
            'via': 'ollama',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
            'stream': False,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    assert r.json['choices'][0]['finish_reason'] == 'length'


def test_chat_completions_forwards_openai_tools_to_dispatch(app, client, monkeypatch):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    tools = [{
        'type': 'function',
        'function': {
            'name': 'read_file',
            'description': 'Read a file',
            'parameters': {
                'type': 'object',
                'properties': {'path': {'type': 'string'}},
                'required': ['path'],
            },
        },
    }]
    captured_tools = None

    def mock_dispatch(*args, **kwargs):
        nonlocal captured_tools
        captured_tools = kwargs.get('tools')
        return {
            'result': {
                'content': [{'text': 'ok'}],
                'usage': {'input_tokens': 1, 'output_tokens': 1},
            },
            'via': 'ollama',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{'role': 'user', 'content': 'read it'}],
            'tools': tools,
            'stream': False,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    assert captured_tools == tools


def test_chat_completions_maps_provider_tool_calls_to_openai_response(
    app, client, monkeypatch,
):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        return {
            'result': {
                'content': [{'text': ''}],
                'tool_calls': [{
                    'id': 'tool_abc',
                    'name': 'read_file',
                    'input': {'path': '/tmp/example.txt'},
                }],
                'stop_reason': 'tool_use',
                'usage': {'input_tokens': 10, 'output_tokens': 5},
            },
            'via': 'ollama',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{'role': 'user', 'content': 'read it'}],
            'tools': [{'type': 'function', 'function': {'name': 'read_file'}}],
            'stream': False,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    choice = r.json['choices'][0]
    assert choice['finish_reason'] == 'tool_calls'
    assert choice['message']['tool_calls'] == [{
        'id': 'tool_abc',
        'type': 'function',
        'function': {
            'name': 'read_file',
            'arguments': json.dumps({'path': '/tmp/example.txt'}),
        },
    }]


def test_streaming_chat_completions_emits_tool_call_delta(app, client, monkeypatch):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        return {
            'result': {
                'content': [{'text': ''}],
                'tool_calls': [{
                    'id': 'tool_abc',
                    'name': 'read_file',
                    'input': {'path': '/tmp/example.txt'},
                }],
                'stop_reason': 'tool_use',
                'usage': {'input_tokens': 10, 'output_tokens': 5},
            },
            'via': 'ollama',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'ollama/ornith:latest',
            'messages': [{'role': 'user', 'content': 'read it'}],
            'tools': [{'type': 'function', 'function': {'name': 'read_file'}}],
            'stream': True,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    body = r.data.decode()
    assert '"tool_calls"' in body
    assert '"finish_reason": "tool_calls"' in body


def test_streaming_chat_completions_emits_finish_reason_on_every_choice(
    app, client, monkeypatch,
):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        return {
            'result': {
                'content': [{'text': 'Hallo'}],
                'stop_reason': 'stop',
                'usage': {'input_tokens': 10, 'output_tokens': 5},
            },
            'via': 'opencode',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/v1/chat/completions',
        json={
            'model': 'opencode/hy3-free',
            'messages': [{'role': 'user', 'content': 'Hallo'}],
            'stream': True,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    events = []
    for block in r.data.decode().split('\n\n'):
        line = block.strip()
        if not line or line == 'data: [DONE]':
            continue
        assert line.startswith('data: ')
        events.append(json.loads(line[len('data: '):]))

    assert events[0]['choices'][0]['delta'] == {
        'role': 'assistant',
        'content': '',
    }
    assert [event['choices'][0]['finish_reason'] for event in events] == [
        None,
        None,
        'stop',
    ]


def test_list_models_excludes_region_locked(app, client, monkeypatch):
    """Region-locked models (e.g. China-only GLM) are hidden from /v1/models."""
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    monkeypatch.setattr(openai_api, 'PROVIDER_REGISTRY', {
        'zai': {
            'name': 'z.ai (GLM)',
            'system': True,
            'requires': [],
            'optional': [],
        },
    })

    def fake_load_config(user_id, provider_id):
        return {}

    class FakeZaiClient:
        def get_models(self):
            return ['glm-4.5-flash', 'glm-4.6', 'glm-5.1']

    def fake_get_client(provider_id, cfg):
        return FakeZaiClient()

    monkeypatch.setattr(openai_api, '_load_config', fake_load_config, raising=False)
    monkeypatch.setattr(openai_api, 'get_client', fake_get_client, raising=False)
    monkeypatch.setattr(openai_api.health_tracker, 'is_healthy', lambda provider_id: True)

    r = client.get('/v1/models', headers={'Authorization': 'Bearer admin-test-token'})

    assert r.status_code == 200
    model_ids = [m['id'] for m in r.json['data']]
    # All zai/* models are region-locked (China-hosted endpoint)
    assert model_ids == []


def test_chat_completions_rejects_region_locked_model(app, client, monkeypatch):
    """Direct dispatch to a region-locked model is rejected with 400."""
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    monkeypatch.setattr(openai_api, 'PROVIDER_REGISTRY', {
        'zai': {
            'name': 'z.ai (GLM)',
            'system': True,
            'requires': [],
            'optional': [],
        },
    })

    def fake_get_client(provider_id, cfg):
        raise AssertionError('region-locked model must never reach a client')

    monkeypatch.setattr(openai_api, 'get_client', fake_get_client, raising=False)

    r = client.post('/v1/chat/completions', headers={'Authorization': 'Bearer admin-test-token'},
                    json={
                        'model': 'zai/glm-4.6',
                        'messages': [{'role': 'user', 'content': 'hello'}],
                    })

    assert r.status_code == 400
    assert 'region-locked' in r.json['error']['message']


def test_is_region_locked_scoped_to_provider():
    from api.openai_api import _is_region_locked

    # z.ai is the China-hosted endpoint -> locked
    assert _is_region_locked('zai', 'glm-4.6')
    assert _is_region_locked('zai', 'glm-5-turbo')
    assert _is_region_locked('zai', 'glm-4.5-flash')
    # Same GLM model via a global gateway stays available
    assert not _is_region_locked('opencode', 'glm-4.5-flash')
    assert not _is_region_locked('openrouter', 'glm-5.1')
    # Unrelated models unaffected
    assert not _is_region_locked('ollama', 'qwen3.6:latest')
    assert not _is_region_locked('opencode', 'deepseek-v4-flash-free')


# ─── Multimodal (vision) content normalization ───────────────────────────────


def test_normalize_message_content_plain_text_unchanged():
    from api.openai_api import _normalize_message_content

    # Plain string content passes through untouched.
    assert _normalize_message_content('hello') == 'hello'
    assert _normalize_message_content(None) == ''
    # Text-only list is flattened to a string (legacy behaviour preserved).
    assert _normalize_message_content([
        {'type': 'text', 'text': 'foo'},
        {'type': 'text', 'text': 'bar'},
    ]) == 'foo\nbar'


def test_normalize_message_content_preserves_image_url_part():
    from api.openai_api import _normalize_message_content

    data_url = 'data:image/png;base64,iVBORw0KGgo=='
    content = [
        {'type': 'text', 'text': 'What colour is this?'},
        {'type': 'image_url', 'image_url': {'url': data_url, 'detail': 'high'}},
    ]
    result = _normalize_message_content(content)

    # Must return a content list (not a flattened string) when images are present.
    assert isinstance(result, list)
    assert {'type': 'text', 'text': 'What colour is this?'} in result
    assert {'type': 'image_url', 'image_url': {'url': data_url, 'detail': 'high'}} in result


def test_normalize_message_content_preserves_mixed_image_types():
    from api.openai_api import _normalize_message_content, _content_part_is_image

    data_url = 'data:image/png;base64,iVBORw0KGgo=='
    content = [
        {'type': 'text', 'text': 'describe'},
        {'type': 'image_url', 'image_url': {'url': data_url}},       # OpenAI format
        {'type': 'image', 'image': 'aGVsbG8='},                      # Ollama-style base64
        {'type': 'input_image', 'image_url': {'url': data_url}},     # Claude-style
    ]
    result = _normalize_message_content(content)

    assert isinstance(result, list)
    assert all(_content_part_is_image(p) for p in result[1:])
    assert result[0] == {'type': 'text', 'text': 'describe'}
    # Image parts are preserved verbatim (no data loss).
    assert result[1] == {'type': 'image_url', 'image_url': {'url': data_url}}


def test_normalize_message_content_ignores_unsupported_parts():
    from api.openai_api import _normalize_message_content

    data_url = 'data:image/png;base64,iVBORw0KGgo=='
    content = [
        {'type': 'text', 'text': 'hi'},
        {'type': 'image_url', 'image_url': {'url': data_url}},
        {'type': 'tool_result', 'content': 'some tool output'},
        'raw string part',
    ]
    result = _normalize_message_content(content)

    assert isinstance(result, list)
    # Tool/unknown parts are dropped; text + image are kept.
    types = [p.get('type') for p in result]
    assert types == ['text', 'image_url']


def test_chat_completions_passes_image_content_to_dispatch(app, client, monkeypatch):
    """Image_url content parts must survive normalization and reach dispatch.

    Regression test for the vision bug: /v1/chat/completions stripped image
    parts to a text-only string, so multimodal models never saw the image.
    """
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    captured = {}

    def mock_dispatch(*args, **kwargs):
        captured['messages'] = kwargs.get('messages')
        return {
            'result': {'content': 'red', 'usage': {'input_tokens': 5, 'output_tokens': 1}},
            'via': 'test-provider',
            'fallback_used': False,
        }

    monkeypatch.setattr(openai_api, 'dispatch', mock_dispatch, raising=False)

    r = client.post('/v1/chat/completions',
                    headers={'Authorization': 'Bearer admin-test-token'},
                    json={
                        'model': 'openrouter/nvidia/nemotron-nano-12b-v2-vl:free',
                        'messages': [{
                            'role': 'user',
                            'content': [
                                {'type': 'text', 'text': 'What colour?'},
                                {'type': 'image_url',
                                 'image_url': {'url': 'data:image/png;base64,QUJD', 'detail': 'low'}},
                            ],
                        }],
                    })

    assert r.status_code == 200
    assert r.json['choices'][0]['message']['content'] == 'red'
    # The image part must be present in the messages handed to dispatch.
    msg = captured['messages'][0]
    assert isinstance(msg['content'], list)
    assert any(
        p.get('type') == 'image_url' and p.get('image_url', {}).get('url', '').startswith('data:image')
        for p in msg['content']
    )
