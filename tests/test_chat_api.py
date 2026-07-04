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
