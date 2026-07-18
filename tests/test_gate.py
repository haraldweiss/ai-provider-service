# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for is_allowed() and require_provider_access decorator."""

import pytest
from flask import jsonify
from config import Config
from database import db
from storage.models import ProviderGrant
from api.auth import Principal
from api.gate import is_allowed


@pytest.fixture
def gate_on(app):
    Config.GATE_ENABLED = True
    Config.UNGATED_PROVIDERS = {'ollama'}
    return app


def test_ungated_provider_always_allowed(gate_on):
    with gate_on.app_context():
        assert is_allowed(Principal('lisa', 'user'), 'ollama') is True


def test_admin_bypasses_gate(gate_on):
    with gate_on.app_context():
        assert is_allowed(Principal('harald', 'admin'), 'claude') is True


def test_user_without_grant_denied(gate_on):
    with gate_on.app_context():
        assert is_allowed(Principal('lisa', 'user'), 'claude') is False


def test_user_with_active_grant_allowed(gate_on):
    with gate_on.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()
        assert is_allowed(Principal('lisa', 'user'), 'claude') is True


def test_user_with_revoked_grant_denied(gate_on):
    from datetime import datetime, timezone
    with gate_on.app_context():
        g_row = ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald',
            revoked_at=datetime.now(timezone.utc))
        db.session.add(g_row)
        db.session.commit()
        assert is_allowed(Principal('lisa', 'user'), 'claude') is False


def test_gate_disabled_allows_everything(app):
    Config.GATE_ENABLED = False
    with app.app_context():
        assert is_allowed(Principal('anyone', 'user'), 'claude') is True


def test_decorator_403s_when_denied(gate_on, client):
    from api.gate import require_provider_access
    from api.auth import require_token

    @gate_on.route('/_t/use/<provider_id>')
    @require_token
    @require_provider_access('provider_id')
    def use(provider_id):
        return jsonify({'ok': True})

    r = client.get('/_t/use/claude?user_id=lisa',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 403
    assert r.get_json()['error'] == 'needs_approval'


def test_decorator_allows_when_granted(gate_on, client):
    from api.gate import require_provider_access
    from api.auth import require_token

    with gate_on.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

    @gate_on.route('/_t/use2/<provider_id>')
    @require_token
    @require_provider_access('provider_id')
    def use2(provider_id):
        return jsonify({'ok': True})

    r = client.get('/_t/use2/claude?user_id=lisa',
                   headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 200


def test_decorator_extracts_provider_from_chat_body(gate_on, client):
    """The /chat endpoint passes 'provider' in JSON body, not 'provider_id'.
    The decorator must fall back to body.get('provider').
    """
    from api.gate import require_provider_access
    from api.auth import require_token

    @gate_on.route('/_t/chat-shape', methods=['POST'])
    @require_token
    @require_provider_access('provider_id')
    def chat_shape():
        return jsonify({'ok': True})

    # ollama is ungated → 200 even without grant
    r = client.post('/_t/chat-shape',
                    json={'user_id': 'lisa', 'provider': 'ollama'},
                    headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 200

    # claude is gated; no grant → 403
    r = client.post('/_t/chat-shape',
                    json={'user_id': 'lisa', 'provider': 'claude'},
                    headers={'Authorization': 'Bearer test-token'})
    assert r.status_code == 403
