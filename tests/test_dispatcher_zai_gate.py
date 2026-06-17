# SPDX-License-Identifier: AGPL-3.0-or-later
"""z.ai Server-Key Allowlist im Dispatcher.

Der zentrale ZAI_API_KEY ist nur für den Owner (Allowlist) freigeschaltet.
Alle anderen User müssen einen eigenen Key via ProviderConfig konfigurieren —
sonst liefert `_load_config` None (Provider nicht verfügbar).
"""
from __future__ import annotations
import dispatcher
from dispatcher import _load_config
from database import db
from storage.models import ProviderConfig


def test_zai_owner_gets_system_key(app, monkeypatch):
    """Owner (ADMIN_USER_ID) ohne eigene Config → leeres Dict (System-Key)."""
    monkeypatch.setattr(dispatcher.Config, 'ZAI_SERVER_KEY_ALLOWED_USERS', '')
    monkeypatch.setattr(dispatcher.Config, 'ADMIN_USER_ID', 'harald')
    assert _load_config('harald', 'zai') == {}


def test_zai_other_user_denied_without_own_config(app, monkeypatch):
    """Nicht-Owner ohne eigene Config → None (kein System-Key-Zugriff)."""
    monkeypatch.setattr(dispatcher.Config, 'ZAI_SERVER_KEY_ALLOWED_USERS', '')
    monkeypatch.setattr(dispatcher.Config, 'ADMIN_USER_ID', 'harald')
    assert _load_config('eve', 'zai') is None


def test_zai_other_user_with_own_key(app, monkeypatch):
    """Nicht-Owner mit eigener ProviderConfig → bekommt seinen eigenen Key."""
    monkeypatch.setattr(dispatcher.Config, 'ZAI_SERVER_KEY_ALLOWED_USERS', '')
    monkeypatch.setattr(dispatcher.Config, 'ADMIN_USER_ID', 'harald')
    pc = ProviderConfig(user_id='eve', provider_id='zai')
    pc.set_config({'api_key': 'eve-own-key'})
    db.session.add(pc)
    db.session.commit()

    cfg = _load_config('eve', 'zai')
    assert cfg == {'api_key': 'eve-own-key'}


def test_zai_allowlist_env_overrides_default(app, monkeypatch):
    """Gesetzte Allowlist ersetzt den ADMIN_USER_ID-Default."""
    monkeypatch.setattr(dispatcher.Config, 'ZAI_SERVER_KEY_ALLOWED_USERS', 'alice,bob')
    monkeypatch.setattr(dispatcher.Config, 'ADMIN_USER_ID', 'harald')
    assert _load_config('alice', 'zai') == {}
    # Owner-Default greift NICHT mehr, wenn Allowlist explizit gesetzt ist
    assert _load_config('harald', 'zai') is None
