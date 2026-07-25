"""Tests for the provider-agnostic /chat endpoint."""


def test_chat_returns_503_for_provider_unavailable(app, client, monkeypatch):
    from config import Config
    import api.chat_api as chat_api
    from dispatcher import ProviderUnavailableError

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        raise ProviderUnavailableError(
            'Provider ollama nicht erreichbar, kein Fallback/Queue konfiguriert'
        )

    monkeypatch.setattr(chat_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/chat',
        json={
            'user_id': 'harald',
            'provider': 'ollama',
            'model': 'ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 503
    assert r.json['error'] == (
        'Provider ollama nicht erreichbar, kein Fallback/Queue konfiguriert'
    )


def test_chat_returns_400_when_provider_rejects_request(app, client, monkeypatch):
    from config import Config
    import api.chat_api as chat_api
    from dispatcher import ProviderRequestError

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        raise ProviderRequestError('omlx', 400)

    monkeypatch.setattr(chat_api, 'dispatch', mock_dispatch)

    response = client.post(
        '/chat',
        json={
            'user_id': 'harald',
            'provider': 'omlx',
            'model': 'Devstral-Small-2-24B-Instruct-2512-4bit',
            'messages': [{'role': 'user', 'content': 'test'}],
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert response.status_code == 400
    assert response.json['error'] == 'Provider omlx rejected the request (HTTP 400)'


def test_chat_treats_null_max_tokens_as_default(app, client, monkeypatch):
    """max_tokens: null previously crashed with TypeError -> 500 (int(None))."""
    from config import Config
    import api.chat_api as chat_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    captured = {}

    def mock_dispatch(*args, **kwargs):
        captured.update(kwargs)
        return {'result': {}, 'via': 'ollama', 'fallback_used': False}

    monkeypatch.setattr(chat_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/chat',
        json={
            'user_id': 'harald',
            'provider': 'ollama',
            'model': 'ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': None,
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    assert captured['max_tokens'] == 600


def test_chat_returns_400_for_invalid_max_tokens(app, client):
    from config import Config

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    r = client.post(
        '/chat',
        json={
            'user_id': 'harald',
            'provider': 'ollama',
            'model': 'ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
            'max_tokens': 'not-a-number',
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 400
    assert 'max_tokens' in r.json['error']


def test_chat_returns_400_for_non_list_messages(app, client):
    from config import Config

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    r = client.post(
        '/chat',
        json={
            'user_id': 'harald',
            'provider': 'ollama',
            'model': 'ornith:latest',
            'messages': 'just-a-string',
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 400


def test_chat_fallbacks_on_429_from_primary(app, client, monkeypatch):
    """429 vom Primary-Provider löst Fallback aus, z.B. bei opencode Insufficient Balance."""
    from config import Config
    import api.chat_api as chat_api
    from dispatcher import ProviderRequestError

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    captured = {}

    def mock_dispatch(*args, **kwargs):
        captured.update(kwargs)
        return {
            'result': {'text': 'fallback response', 'usage': {}},
            'via': 'openai', 'model': 'gpt-4o-free',
            'fallback_used': True, 'primary_provider': 'openai',
            'primary_model': 'gpt-4o',
        }

    monkeypatch.setattr(chat_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/chat',
        json={
            'user_id': 'harald',
            'provider': 'openai',
            'model': 'gpt-4o',
            'messages': [{'role': 'user', 'content': 'ping'}],
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 200
    assert r.json['fallback_used'] is True
    assert r.json['via'] == 'openai'


def test_chat_returns_429_when_no_fallback_and_all_providers_rate_limited(app, client, monkeypatch):
    """Wenn weder Fallback noch Queue konfiguriert ist, wird 429 durchgereicht."""
    from config import Config
    import api.chat_api as chat_api
    from dispatcher import ProviderRequestError

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    def mock_dispatch(*args, **kwargs):
        raise ProviderRequestError('ollama', 429)

    monkeypatch.setattr(chat_api, 'dispatch', mock_dispatch)

    r = client.post(
        '/chat',
        json={
            'user_id': 'harald',
            'provider': 'ollama',
            'model': 'ornith:latest',
            'messages': [{'role': 'user', 'content': 'ping'}],
        },
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert r.status_code == 429
    assert 'HTTP 429' in r.json['error']
