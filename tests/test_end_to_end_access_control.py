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


def test_e2e_personal_key_bypasses_then_restores_grant_gate(client, app):
    # Personal key setup succeeds without an owner-funded grant.
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200

    # Removing the personal key removes BYO authorization.
    r = client.delete(
        '/configs/lisa/claude',
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200

    r = client.post('/chat', json={
        'user_id': 'lisa', 'provider': 'claude', 'model': 'claude-test',
        'messages': [{'role': 'user', 'content': 'hello'}],
    },
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403
