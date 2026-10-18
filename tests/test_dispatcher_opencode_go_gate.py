# SPDX-License-Identifier: AGPL-3.0-or-later
"""opencode_go Server-Key: Config-Auflösung + Allowlist im Dispatcher.

Context (AGENTS §7, 2026-10-18): OpenCode invalidated the Console key, so the
gateway moved to the Go plan. The Go server key is open to all users by default
(that is how `opencode` was funded before), unlike z.ai which defaults to the
owner only. A set allowlist replaces that default.
"""
from __future__ import annotations

import dispatcher
from dispatcher import _load_config
from database import db
from storage.models import ProviderConfig


def test_go_server_key_open_for_all_users_by_default(app, monkeypatch):
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_API_KEY', 'oc_sk_server')
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_SERVER_KEY_ALLOWED_USERS', '')
    assert _load_config('lisa', 'opencode_go') == {'api_key': 'oc_sk_server'}


def test_go_server_key_config_has_no_free_only_flag(app, monkeypatch):
    """`_free_only` exists for Zen's app-locked free tier; the Go plan covers
    its whole catalog, so the flag must not be set (it would hide models)."""
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_API_KEY', 'oc_sk_server')
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_SERVER_KEY_ALLOWED_USERS', '')
    cfg = _load_config('harald', 'opencode_go')
    assert '_free_only' not in cfg


def test_go_without_server_key_is_unavailable(app, monkeypatch):
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_API_KEY', '')
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_SERVER_KEY_ALLOWED_USERS', '')
    assert _load_config('lisa', 'opencode_go') is None


def test_go_allowlist_restricts_server_key(app, monkeypatch):
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_API_KEY', 'oc_sk_server')
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_SERVER_KEY_ALLOWED_USERS',
                        'harald,wolfinichat')
    assert _load_config('harald', 'opencode_go') == {'api_key': 'oc_sk_server'}
    assert _load_config('wolfinichat', 'opencode_go') == {'api_key': 'oc_sk_server'}
    assert _load_config('lisa', 'opencode_go') is None


def test_go_personal_key_wins_over_allowlist(app, monkeypatch):
    """A user's own Go key must be used even when the server key is restricted."""
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_API_KEY', 'oc_sk_server')
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_GO_SERVER_KEY_ALLOWED_USERS',
                        'harald')
    pc = ProviderConfig(user_id='lisa', provider_id='opencode_go')
    pc.set_config({'api_key': 'lisa-own-go-key'})
    db.session.add(pc)
    db.session.commit()

    assert _load_config('lisa', 'opencode_go') == {'api_key': 'lisa-own-go-key'}


def test_zen_opencode_still_free_only(app, monkeypatch):
    """The Zen provider keeps its free-only system-key behaviour."""
    monkeypatch.setattr(dispatcher.Config, 'OPENCODE_API_KEY', 'sk-zen-server')
    assert _load_config('lisa', 'opencode') == {
        '_free_only': True, 'api_key': 'sk-zen-server'}
