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


def test_admin_ui_login_page_renders(client):
    r = client.get('/admin/ui/login')
    assert r.status_code == 200
    assert b'admin' in r.data.lower()


def test_admin_ui_logout_clears_session(client):
    client.get('/admin/ui/?token=admin-test-token', follow_redirects=False)
    r = client.get('/admin/ui/logout', follow_redirects=False)
    assert _is_redirect(r.status_code)
    r2 = client.get('/admin/ui/', follow_redirects=False)
    assert 'login' in r2.location.lower()
