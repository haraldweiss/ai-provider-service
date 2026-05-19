# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für Per-Request-Fallback im Dispatcher.

Verifiziert, dass `dispatch()` Per-Request-Override-Felder
(fallback_provider_override, fallback_model_override, fallback_config_override)
korrekt verwendet und Vorrang vor der DB-ProviderConfig haben.
"""
from __future__ import annotations
import sys
import os
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app import create_app
from database import db


@pytest.fixture
def app():
    """Flask-App + In-Memory SQLite für isolierte Tests."""
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['ENCRYPTION_KEY'] = 'X' * 44  # fake 32-byte base64
    os.environ['SERVICE_TOKEN'] = 'test-token'
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_dispatch_uses_request_fallback_when_primary_down(app):
    """Per-Request-Fallback wird verwendet, wenn Primary fehlschlägt."""
    from dispatcher import dispatch

    with patch('dispatcher.health_tracker.is_healthy', return_value=False), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.return_value = {'content': [{'text': 'ok'}], 'usage': {}}

        result = dispatch(
            user_id='user-1', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
            fallback_provider_override='claude',
            fallback_model_override='claude-haiku-4-5-20251001',
        )

        # Primary down → erster _execute-Call wurde übersprungen (nur fallback called)
        assert mock_exec.call_count == 1
        call_args = mock_exec.call_args
        assert call_args.args[1] == 'claude'  # provider_id = fallback
        assert call_args.args[2] == 'claude-haiku-4-5-20251001'  # model = override
        assert result['fallback_used'] is True
        assert result['via'] == 'claude'
        # Echtes Fallback-Modell wird im Response zurueckgegeben (Cost-Tracking)
        assert result['model'] == 'claude-haiku-4-5-20251001'
        assert result.get('primary_model') == 'qwen:latest'


def test_dispatch_primary_returns_model_field(app):
    """Primary-Path: model-Feld im Response = das genutzte Primary-Modell."""
    from dispatcher import dispatch

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.return_value = {'content': [{'text': 'ok'}], 'usage': {}}

        result = dispatch(
            user_id='user-1', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
        )
        assert result['fallback_used'] is False
        assert result['via'] == 'ollama'
        assert result['model'] == 'qwen:latest'


def test_dispatch_request_fallback_overrides_db_fallback(app):
    """Per-Request-Fallback gewinnt vor DB-stored ProviderConfig.fallback_provider."""
    from dispatcher import dispatch
    from storage.models import ProviderConfig

    # DB: Ollama hat openai als fallback gespeichert
    pc = ProviderConfig(
        user_id='user-2', provider_id='ollama',
        fallback_provider='openai',
    )
    pc.set_config({})
    db.session.add(pc)
    db.session.commit()

    with patch('dispatcher.health_tracker.is_healthy', return_value=False), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.return_value = {'content': [], 'usage': {}}

        # Request übergibt 'claude' als Override
        dispatch(
            user_id='user-2', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
            fallback_provider_override='claude',
        )

        # Erwartung: claude wurde verwendet, nicht openai aus der DB
        assert mock_exec.call_args.args[1] == 'claude'


def test_dispatch_falls_back_to_db_when_no_request_override(app):
    """Ohne Per-Request-Override wird DB-ProviderConfig.fallback_provider genutzt."""
    from dispatcher import dispatch
    from storage.models import ProviderConfig

    pc = ProviderConfig(
        user_id='user-3', provider_id='ollama',
        fallback_provider='claude',
    )
    pc.set_config({})
    db.session.add(pc)
    db.session.commit()

    with patch('dispatcher.health_tracker.is_healthy', return_value=False), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.return_value = {'content': [], 'usage': {}}

        dispatch(
            user_id='user-3', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
        )

        # Erwartung: claude aus DB wurde verwendet
        assert mock_exec.call_args.args[1] == 'claude'


def test_dispatch_fallback_config_override_skips_db_load(app):
    """fallback_config_override wird an _execute durchgereicht (nicht aus DB geladen)."""
    from dispatcher import dispatch

    with patch('dispatcher.health_tracker.is_healthy', return_value=False), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.return_value = {'content': [], 'usage': {}}

        dispatch(
            user_id='user-4', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
            fallback_provider_override='claude',
            fallback_config_override={'api_key': 'sk-test'},
        )

        # config_override (6. Positional) sollte das dict sein
        call_args = mock_exec.call_args
        assert call_args.args[5] == {'api_key': 'sk-test'}


def test_dispatch_keeps_original_model_if_no_fallback_model_override(app):
    """Wenn fallback_model_override fehlt, wird das original model auch für Fallback genutzt."""
    from dispatcher import dispatch

    with patch('dispatcher.health_tracker.is_healthy', return_value=False), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.return_value = {'content': [], 'usage': {}}

        dispatch(
            user_id='user-5', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
            fallback_provider_override='claude',
            # fallback_model_override absichtlich weggelassen
        )

        # _execute wird mit dem original model aufgerufen
        assert mock_exec.call_args.args[2] == 'qwen:latest'
