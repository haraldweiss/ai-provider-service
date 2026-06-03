# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for admin UI routes — auth flow and page rendering."""

import pytest
from config import Config


@pytest.fixture(autouse=True)
def setup_admin():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.SECRET_KEY = 'test-secret-key-for-sessions'


def _is_redirect(status):
    return status in (302, 303, 308)


def test_admin_ui_root_redirects_to_users_when_authed(client):
    client.get('/admin/ui/?token=admin-test-token', follow_redirects=False)
    r = client.get('/admin/ui/', follow_redirects=False)
    assert _is_redirect(r.status_code)
    assert '/admin/ui/users' in r.location


def test_admin_ui_root_redirects_to_login_when_not_authed(client):
    r = client.get('/admin/ui/', follow_redirects=False)
    assert _is_redirect(r.status_code)
    assert 'login' in r.location.lower()


def test_admin_ui_invalid_token_redirects_to_login(client):
    r = client.get('/admin/ui/?token=wrong-token', follow_redirects=False)
    assert _is_redirect(r.status_code)
    assert 'login' in r.location.lower()


def test_admin_ui_login_redirects_to_root_with_token(client):
    r = client.get('/admin/ui/login', follow_redirects=False)
    assert _is_redirect(r.status_code)
    assert '/admin/ui/' in r.location
    assert 'token=admin-test-token' in r.location


def test_users_page_lists_known_users(client, app):
    from database import db
    from storage.models import ProviderConfig, ProviderGrant

    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    client.get('/admin/ui/?token=admin-test-token', follow_redirects=False)
    r = client.get('/admin/ui/users')
    assert r.status_code == 200
    assert b'lisa' in r.data
    assert b'claude' in r.data


def test_user_detail_page_shows_grants_and_configs(client, app):
    from database import db
    from storage.models import ProviderConfig, ProviderGrant

    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    client.get('/admin/ui/?token=admin-test-token', follow_redirects=False)
    r = client.get('/admin/ui/users/lisa')
    assert r.status_code == 200
    assert b'ollama' in r.data
    assert b'claude' in r.data
    assert b'approve' in r.data.lower() or b'revoke' in r.data.lower()


def test_admin_ui_logout_clears_session(client):
    client.get('/admin/ui/?token=admin-test-token', follow_redirects=False)
    r = client.get('/admin/ui/logout', follow_redirects=False)
    assert _is_redirect(r.status_code)
    r2 = client.get('/admin/ui/', follow_redirects=False)
    assert 'login' in r2.location.lower()
