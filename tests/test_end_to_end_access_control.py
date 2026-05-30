# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end: simulate the full admin → grant → consumer flow."""

import pytest
from config import Config


@pytest.fixture(autouse=True)
def gated_app():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.SECRET_KEY = 'test-secret'
    Config.GATE_ENABLED = True
    Config.UNGATED_PROVIDERS = {'ollama'}
    yield
    Config.GATE_ENABLED = False


def test_e2e_user_blocked_then_granted_then_configured(client, app):
    from database import db

    # Step 1: New user 'lisa' tries to configure claude — blocked.
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403

    # Step 2: Admin grants claude to lisa.
    r = client.post(
        '/admin/grants',
        json={'user_id': 'lisa', 'provider_id': 'claude',
              'note': 'transcript summaries'},
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert r.status_code == 201
    grant_id = r.get_json()['grant']['id']

    # Step 3: lisa retries — succeeds.
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200

    # Step 4: Admin revokes.
    r = client.delete(
        f'/admin/grants/{grant_id}',
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert r.status_code == 204

    # Step 5: lisa is blocked again on next config attempt.
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403
