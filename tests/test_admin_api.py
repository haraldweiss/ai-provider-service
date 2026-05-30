# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for /admin/grants CRUD endpoints."""

import pytest
from config import Config
from database import db
from storage.models import ProviderGrant


@pytest.fixture(autouse=True)
def admin_token():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.ADMIN_USER_ID = 'harald'


def H_admin():
    return {'Authorization': 'Bearer admin-test-token'}


def H_user():
    return {'Authorization': 'Bearer test-token'}


def test_post_grant_requires_admin(client):
    r = client.post('/admin/grants',
                    json={'user_id': 'lisa', 'provider_id': 'claude'},
                    headers=H_user())
    assert r.status_code == 403


def test_post_grant_creates_row(client, app):
    r = client.post('/admin/grants',
                    json={'user_id': 'lisa', 'provider_id': 'claude',
                          'note': 'test reason'},
                    headers=H_admin())
    assert r.status_code == 201
    g = r.get_json()['grant']
    assert g['user_id'] == 'lisa'
    assert g['provider_id'] == 'claude'
    assert g['granted_by'] == 'harald'
    assert g['note'] == 'test reason'
    assert g['revoked_at'] is None


def test_post_grant_idempotent_restores_revoked(client, app):
    from datetime import datetime, timezone
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald',
            revoked_at=datetime.now(timezone.utc)))
        db.session.commit()

    r = client.post('/admin/grants',
                    json={'user_id': 'lisa', 'provider_id': 'claude'},
                    headers=H_admin())
    assert r.status_code == 201
    assert r.get_json()['grant']['revoked_at'] is None


def test_get_grants_lists(client, app):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.add(ProviderGrant(
            user_id='bob', provider_id='openai', granted_by='harald'))
        db.session.commit()

    r = client.get('/admin/grants', headers=H_admin())
    assert r.status_code == 200
    grants = r.get_json()['grants']
    assert len(grants) == 2


def test_get_grants_filters_by_user(client, app):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.add(ProviderGrant(
            user_id='bob', provider_id='openai', granted_by='harald'))
        db.session.commit()

    r = client.get('/admin/grants?user_id=lisa', headers=H_admin())
    assert r.status_code == 200
    grants = r.get_json()['grants']
    assert len(grants) == 1
    assert grants[0]['user_id'] == 'lisa'


def test_get_grants_excludes_revoked_by_default(client, app):
    from datetime import datetime, timezone
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald',
            revoked_at=datetime.now(timezone.utc)))
        db.session.commit()

    r = client.get('/admin/grants', headers=H_admin())
    assert r.get_json()['grants'] == []

    r2 = client.get('/admin/grants?include_revoked=true', headers=H_admin())
    assert len(r2.get_json()['grants']) == 1


def test_delete_grant_soft_deletes(client, app):
    with app.app_context():
        g = ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald')
        db.session.add(g)
        db.session.commit()
        grant_id = g.id

    r = client.delete(f'/admin/grants/{grant_id}', headers=H_admin())
    assert r.status_code == 204

    with app.app_context():
        fresh = db.session.get(ProviderGrant, grant_id)
        assert fresh.revoked_at is not None


def test_delete_unknown_grant_404(client):
    r = client.delete('/admin/grants/99999', headers=H_admin())
    assert r.status_code == 404
