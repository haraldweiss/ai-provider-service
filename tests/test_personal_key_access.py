import pytest

from api.auth import Principal
from api.gate import is_allowed
from database import db
from storage.models import ProviderConfig
from storage.user_tokens import issue_user_token


@pytest.fixture(autouse=True)
def gated(monkeypatch):
    import api.gate as gate
    monkeypatch.setattr(gate.Config, 'GATE_ENABLED', True)
    monkeypatch.setattr(gate.Config, 'UNGATED_PROVIDERS', {'ollama'})


@pytest.mark.parametrize(
    'provider_id', ['claude', 'opencode', 'openai', 'zai', 'ollama_cloud'],
)
def test_personal_key_authorizes_without_grant(app, provider_id):
    pc = ProviderConfig(user_id='lisa', provider_id=provider_id)
    pc.set_config({'api_key': 'personal-test-key'})
    db.session.add(pc)
    db.session.commit()

    principal = Principal('lisa', 'user', 'user_token')
    assert is_allowed(principal, provider_id) is True


def test_empty_or_corrupt_config_does_not_authorize(app):
    empty = ProviderConfig(user_id='lisa', provider_id='claude')
    empty.set_config({})
    corrupt = ProviderConfig(
        user_id='eve', provider_id='claude', config_encrypted='not-fernet',
    )
    db.session.add_all([empty, corrupt])
    db.session.commit()

    assert is_allowed(Principal('lisa', 'user'), 'claude') is False
    assert is_allowed(Principal('eve', 'user'), 'claude') is False


def test_user_can_save_and_remove_own_key_without_grant(client, app):
    raw = issue_user_token('lisa')
    headers = {'Authorization': f'Bearer {raw}'}

    saved = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'personal-key'}},
        headers=headers,
    )
    assert saved.status_code == 200
    assert saved.get_json()['has_api_key'] is True

    removed = client.delete('/configs/lisa/claude', headers=headers)
    assert removed.status_code == 200
    assert is_allowed(Principal('lisa', 'user'), 'claude') is False


def test_user_cannot_create_non_key_config_without_grant(client, app):
    raw = issue_user_token('lisa')
    response = client.post(
        '/configs/lisa/claude', json={'config': {}},
        headers={'Authorization': f'Bearer {raw}'},
    )
    assert response.status_code == 403


def test_personal_key_never_authorizes_another_user(app):
    pc = ProviderConfig(user_id='lisa', provider_id='claude')
    pc.set_config({'api_key': 'personal-test-key'})
    db.session.add(pc)
    db.session.commit()
    assert is_allowed(Principal('eve', 'user'), 'claude') is False
