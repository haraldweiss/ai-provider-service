from database import db
from storage.models import ProviderConfig


def _disable_server_keys(monkeypatch):
    from config import Config
    import dispatcher

    monkeypatch.setenv('CLAUDE_SERVER_KEY_ALLOWED_USERS', 'harald')
    monkeypatch.setattr(Config, 'ANTHROPIC_API_KEY', '')
    monkeypatch.setattr(Config, 'OPENCODE_API_KEY', '')
    monkeypatch.setattr(Config, 'ZAI_API_KEY', '')
    monkeypatch.setattr(Config, 'ZAI_SERVER_KEY_ALLOWED_USERS', '')
    monkeypatch.setattr(dispatcher.Config, 'ANTHROPIC_API_KEY', '')
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_API_KEY', '')
    monkeypatch.setattr(dispatcher.Config, 'ZAI_API_KEY', '')
    monkeypatch.setattr(dispatcher.Config, 'ZAI_SERVER_KEY_ALLOWED_USERS', '')


def test_providers_hides_key_required_providers_without_user_key(client, monkeypatch):
    from config import Config

    Config.ADMIN_TOKEN = 'admin-test-token'
    _disable_server_keys(monkeypatch)

    response = client.get(
        '/providers?user_id=lisa',
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert response.status_code == 200
    body = response.get_json()
    visible_ids = {p['id'] for p in body['providers']}
    hidden_ids = {p['id'] for p in body['hidden_providers']}

    assert 'ollama' in visible_ids
    assert 'openai' not in visible_ids
    assert 'ollama_cloud' not in visible_ids
    assert {'claude', 'openai', 'opencode', 'zai', 'ollama_cloud', 'openrouter'} <= hidden_ids
    assert body['availability_hint']['hidden_provider_count'] == 6
    assert 'API key' in body['availability_hint']['message']


def test_personal_key_makes_provider_visible(client, app, monkeypatch):
    from config import Config

    Config.ADMIN_TOKEN = 'admin-test-token'
    _disable_server_keys(monkeypatch)

    pc = ProviderConfig(user_id='lisa', provider_id='openai')
    pc.set_config({'api_key': 'personal-test-key'})
    db.session.add(pc)
    db.session.commit()

    response = client.get(
        '/providers?user_id=lisa',
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert response.status_code == 200
    body = response.get_json()
    visible_ids = {p['id'] for p in body['providers']}
    hidden_ids = {p['id'] for p in body['hidden_providers']}

    assert 'openai' in visible_ids
    assert 'openai' not in hidden_ids


def test_models_endpoint_refuses_key_required_provider_without_user_key(client, monkeypatch):
    from config import Config

    Config.ADMIN_TOKEN = 'admin-test-token'
    _disable_server_keys(monkeypatch)

    response = client.get(
        '/providers/openai/models?user_id=lisa',
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert response.status_code == 400
    assert response.get_json()['error'] == 'provider_requires_api_key'


def test_v1_models_returns_availability_hint_for_hidden_key_providers(
    app, client, monkeypatch,
):
    from config import Config
    import api.openai_api as openai_api

    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'lisa'
    _disable_server_keys(monkeypatch)

    monkeypatch.setattr(openai_api.health_tracker, 'is_healthy', lambda provider_id: True)

    class FakeOllamaClient:
        def get_models(self):
            return ['ornith:latest']

    def fake_get_client(provider_id, cfg):
        assert provider_id == 'ollama'
        return FakeOllamaClient()

    monkeypatch.setattr(openai_api, 'get_client', fake_get_client)

    response = client.get(
        '/v1/models',
        headers={'Authorization': 'Bearer admin-test-token'},
    )

    assert response.status_code == 200
    body = response.get_json()
    assert [m['id'] for m in body['data']] == ['ollama/ornith:latest']
    assert {'openai', 'ollama_cloud'} <= {
        p['id'] for p in body['hidden_providers']
    }
    assert body['availability_hint']['hidden_provider_count'] >= 2
