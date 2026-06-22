import pytest

from config import Config
from storage.user_tokens import issue_user_token


@pytest.fixture(autouse=True)
def secret_key(app):
    Config.SECRET_KEY = 'settings-test-secret'
    app.config['SECRET_KEY'] = Config.SECRET_KEY


def _login(client, raw):
    response = client.post('/settings/login', data={'token': raw})
    assert response.status_code == 302
    with client.session_transaction() as session:
        return session['settings_csrf']


def test_login_stores_identity_and_generation_not_plaintext(client, app):
    raw = issue_user_token('lisa')
    _login(client, raw)
    with client.session_transaction() as session:
        assert session['settings_user_id'] == 'lisa'
        assert session['settings_token_generation']
        assert raw not in repr(dict(session))


def test_rotation_invalidates_existing_settings_session(client, app):
    raw = issue_user_token('lisa')
    _login(client, raw)
    issue_user_token('lisa')
    response = client.get('/settings/providers')
    assert response.status_code == 302
    assert '/settings/login' in response.location


def test_state_change_rejects_missing_csrf(client, app):
    raw = issue_user_token('lisa')
    _login(client, raw)
    response = client.post(
        '/settings/providers/claude/save', data={'api_key': 'x'},
    )
    assert response.status_code == 403


def test_save_and_remove_never_render_plaintext_key(client, app):
    raw = issue_user_token('lisa')
    csrf = _login(client, raw)
    saved = client.post('/settings/providers/claude/save', data={
        'csrf_token': csrf, 'api_key': 'top-secret-personal-key',
    })
    assert saved.status_code == 302

    page = client.get('/settings/providers')
    assert page.status_code == 200
    assert b'configured' in page.data.lower()
    assert b'top-secret-personal-key' not in page.data

    removed = client.post('/settings/providers/claude/remove', data={
        'csrf_token': csrf,
    })
    assert removed.status_code == 302


def test_provider_test_error_is_sanitized(client, app, monkeypatch):
    raw = issue_user_token('lisa')
    csrf = _login(client, raw)
    client.post('/settings/providers/claude/save', data={
        'csrf_token': csrf, 'api_key': 'top-secret-personal-key',
    })

    class BrokenClient:
        def get_models(self):
            raise RuntimeError('upstream echoed top-secret-personal-key')

    monkeypatch.setattr('api.settings_ui.get_client', lambda *_: BrokenClient())
    response = client.post('/settings/providers/claude/test', data={
        'csrf_token': csrf,
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b'connection test failed' in response.data.lower()
    assert b'top-secret-personal-key' not in response.data
