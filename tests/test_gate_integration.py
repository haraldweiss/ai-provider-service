# SPDX-License-Identifier: AGPL-3.0-or-later
"""Integration tests: gate applied to /configs, /chat, /providers endpoints."""

import pytest
from config import Config
from database import db
from storage.models import ProviderGrant


@pytest.fixture(autouse=True)
def enable_gate():
    Config.ADMIN_TOKEN = 'admin-test-token'
    Config.GATE_ENABLED = True
    Config.UNGATED_PROVIDERS = {'ollama'}
    yield
    Config.GATE_ENABLED = False


def test_save_config_ollama_works_without_grant(client):
    r = client.post(
        '/configs/lisa/ollama',
        json={'config': {}, 'fallback_provider': None},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200


def test_save_personal_claude_key_allowed_without_grant(client):
    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200
    assert r.get_json()['has_api_key'] is True


def test_save_config_claude_allowed_with_grant(app, client):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    r = client.post(
        '/configs/lisa/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 200


def test_save_config_admin_token_bypasses_gate(client):
    r = client.post(
        '/configs/harald/claude',
        json={'config': {'api_key': 'sk-test'}},
        headers={'Authorization': 'Bearer admin-test-token'},
    )
    assert r.status_code == 200


def test_chat_blocks_claude_without_grant(client):
    r = client.post(
        '/chat',
        json={
            'user_id': 'lisa',
            'provider': 'claude',
            'model': 'claude-haiku-4-5-20251001',
            'messages': [{'role': 'user', 'content': 'hi'}],
        },
        headers={'Authorization': 'Bearer test-token'},
    )
    assert r.status_code == 403
    assert r.get_json()['error'] == 'needs_approval'
