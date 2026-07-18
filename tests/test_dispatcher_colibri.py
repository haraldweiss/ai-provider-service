# SPDX-License-Identifier: AGPL-3.0-or-later
"""Colibri system-provider access and dispatch protections."""
from unittest.mock import patch

import pytest
from database import db
from storage.models import ProviderConfig


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
    config = ProviderConfig(
        user_id='lisa',
        provider_id='colibri',
        fallback_provider='ollama',
        queue_when_unavailable=True,
    )
    config.set_config({'api_key': 'personal-colibri-key'})
    db.session.add(config)
    db.session.commit()

    with patch('dispatcher.health_tracker.is_healthy') as health_check, \
            patch('dispatcher._execute') as execute, \
            patch('dispatcher.RequestQueue') as request_queue:
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
    execute.assert_not_called()
    request_queue.assert_not_called()


def test_colibri_dispatch_never_queues_for_enabled_admin(app, monkeypatch):
    """A saved generic queue preference cannot queue the Colibri POC."""
    from dispatcher import ProviderUnavailableError, dispatch

    monkeypatch.setattr('dispatcher.Config.COLIBRI_ENABLED', True, raising=False)
    monkeypatch.setattr('dispatcher.Config.ADMIN_USER_ID', 'harald')
    config = ProviderConfig(
        user_id='harald',
        provider_id='colibri',
        queue_when_unavailable=True,
    )
    config.set_config({})
    db.session.add(config)
    db.session.commit()

    with patch('dispatcher.health_tracker.is_healthy', return_value=False) as health_check, \
            patch('dispatcher.RequestQueue') as request_queue, \
            patch('dispatcher.db.session.add') as session_add, \
            patch('dispatcher.db.session.commit') as session_commit:
        with pytest.raises(
            ProviderUnavailableError,
            match='Provider colibri nicht erreichbar, kein Fallback/Queue konfiguriert',
        ):
            dispatch(
                user_id='harald',
                provider_id='colibri',
                model='glm-5.2-colibri',
                messages=[{'role': 'user', 'content': 'hi'}],
            )

    health_check.assert_called_once_with('colibri')
    request_queue.assert_not_called()
    session_add.assert_not_called()
    session_commit.assert_not_called()
