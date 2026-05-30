# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Principal resolution and require_admin decorator."""

import pytest
from flask import g, jsonify
from config import Config


@pytest.fixture
def admin_app(app):
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'

    from api.auth import require_token, require_admin

    @app.route('/_t/who', methods=['GET'])
    @require_token
    def who():
        return jsonify({'user_id': g.principal.user_id, 'role': g.principal.role})

    @app.route('/_t/admin-only', methods=['GET'])
    @require_admin
    def admin_only():
        return jsonify({'ok': True, 'user_id': g.principal.user_id})

    return app


def test_no_token_returns_401(admin_app, client):
    r = client.get('/_t/who')
    assert r.status_code == 401


def test_invalid_token_returns_401(admin_app, client):
    r = client.get('/_t/who', headers={'Authorization': 'Bearer wrong'})
    assert r.status_code == 401


def test_service_token_resolves_to_user_role(admin_app, client):
    r = client.get('/_t/who?user_id=lisa',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['role'] == 'user'
    assert data['user_id'] == 'lisa'


def test_admin_token_resolves_to_admin_role(admin_app, client):
    r = client.get('/_t/who?user_id=ignored-should-be-overridden',
                   headers={'Authorization': 'Bearer admin-test-token'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['role'] == 'admin'
    assert data['user_id'] == 'harald'  # body user_id ignored for admin


def test_require_admin_rejects_service_token(admin_app, client):
    r = client.get('/_t/admin-only',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 403


def test_require_admin_allows_admin_token(admin_app, client):
    r = client.get('/_t/admin-only',
                   headers={'Authorization': 'Bearer admin-test-token'})
    assert r.status_code == 200
    assert r.get_json()['user_id'] == 'harald'
