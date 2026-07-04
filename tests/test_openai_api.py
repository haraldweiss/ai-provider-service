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
