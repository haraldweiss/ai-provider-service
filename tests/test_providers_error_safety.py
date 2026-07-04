from config import Config


class BrokenClient:
    def get_models(self):
        raise RuntimeError('upstream echoed personal-secret-key')


def test_models_error_does_not_echo_provider_exception(client, monkeypatch):
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ANTHROPIC_API_KEY = 'server-test-key'
    monkeypatch.delenv('CLAUDE_SERVER_KEY_ALLOWED_USERS', raising=False)
    monkeypatch.setattr('api.providers_api.get_client', lambda *_: BrokenClient())
    response = client.get(
        '/providers/claude/models?user_id=harald',
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert response.status_code == 502
    assert response.get_json()['error'] == 'provider_request_failed'
    assert b'personal-secret-key' not in response.data


def test_provider_test_error_does_not_echo_provider_exception(client, monkeypatch):
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ANTHROPIC_API_KEY = 'server-test-key'
    monkeypatch.delenv('CLAUDE_SERVER_KEY_ALLOWED_USERS', raising=False)
    monkeypatch.setattr('api.providers_api.get_client', lambda *_: BrokenClient())
    response = client.post(
        '/providers/claude/test', json={'user_id': 'harald'},
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert response.status_code == 400
    assert response.get_json()['error'] == 'provider_request_failed'
    assert b'personal-secret-key' not in response.data
