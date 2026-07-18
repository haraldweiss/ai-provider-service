# SPDX-License-Identifier: AGPL-3.0-or-later
"""Colibri system-provider access and dispatch protections."""
from unittest.mock import patch

import pytest


def test_colibri_only_loads_for_enabled_admin(app, monkeypatch):
    import dispatcher

    monkeypatch.setattr(dispatcher.Config, 'COLIBRI_ENABLED', False, raising=False)
    assert dispatcher._load_config('harald', 'colibri') is None
    monkeypatch.setattr(dispatcher.Config, 'COLIBRI_ENABLED', True, raising=False)
    monkeypatch.setattr(dispatcher.Config, 'ADMIN_USER_ID', 'harald')
    assert dispatcher._load_config('lisa', 'colibri') is None
    assert dispatcher._load_config('harald', 'colibri') == {}


def test_colibri_registry_is_system_only():
    from providers import PROVIDER_REGISTRY, provider_supports_personal_key

    assert PROVIDER_REGISTRY['colibri']['system'] is True
    assert provider_supports_personal_key('colibri') is False


def test_colibri_dispatch_rejects_non_admin_before_fallback_or_queue(app, monkeypatch):
    from dispatcher import ProviderUnavailableError, dispatch

    monkeypatch.setattr('dispatcher.Config.COLIBRI_ENABLED', True, raising=False)
    monkeypatch.setattr('dispatcher.Config.ADMIN_USER_ID', 'harald')
    with patch('dispatcher.health_tracker.is_healthy') as health_check:
        with pytest.raises(
            ProviderUnavailableError,
            match='Provider colibri ist nicht für diesen Benutzer verfügbar',
        ):
            dispatch(
                user_id='lisa',
                provider_id='colibri',
                model='glm-5.2-colibri',
                messages=[{'role': 'user', 'content': 'hi'}],
                fallback_provider_override='ollama',
            )

    health_check.assert_not_called()
