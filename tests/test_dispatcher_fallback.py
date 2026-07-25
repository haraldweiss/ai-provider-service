# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für Per-Request-Fallback im Dispatcher.

Verifiziert, dass `dispatch()` Per-Request-Override-Felder
(fallback_provider_override, fallback_model_override, fallback_config_override)
korrekt verwendet und Vorrang vor der DB-ProviderConfig haben.
"""
from __future__ import annotations
from unittest.mock import Mock, patch

import pytest
import requests

from database import db


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


def test_execute_does_not_mark_provider_unhealthy_when_request_is_rejected(app):
    """An upstream 400 describes this request, not the provider's health."""
    from dispatcher import ProviderRequestError, _execute

    response = Mock(status_code=400)
    rejected_request = requests.HTTPError(response=response)
    client = Mock()
    client.create_message.side_effect = rejected_request

    with patch('dispatcher._load_config', return_value={}), \
         patch('dispatcher.get_client', return_value=client), \
         patch('dispatcher.health_tracker.set_status') as set_status:
        with pytest.raises(ProviderRequestError) as error:
            _execute(
                user_id='harald', provider_id='omlx', model='devstral',
                messages=[{'role': 'user', 'content': 'test'}], max_tokens=16,
            )

    assert error.value.status_code == 400
    assert not any(
        call.args[:2] == ('omlx', False) for call in set_status.call_args_list
    )


def test_execute_treats_sdk_style_4xx_as_a_request_error(app):
    """SDK clients expose status_code directly rather than requests.HTTPError."""
    from dispatcher import ProviderRequestError, _execute

    class SdkStatusError(RuntimeError):
        status_code = 400

    client = Mock()
    client.create_message.side_effect = SdkStatusError('upstream details')

    with patch('dispatcher._load_config', return_value={}), \
         patch('dispatcher.get_client', return_value=client), \
         patch('dispatcher.health_tracker.set_status') as set_status:
        with pytest.raises(ProviderRequestError) as error:
            _execute(
                user_id='harald', provider_id='openai', model='gpt-test',
                messages=[{'role': 'user', 'content': 'test'}], max_tokens=16,
            )

    assert error.value.status_code == 400
    assert not any(
        call.args[:2] == ('openai', False) for call in set_status.call_args_list
    )


def test_dispatch_propagates_provider_request_errors(app):
    """Request validation errors must not enter the fallback/queue path."""
    from dispatcher import ProviderRequestError, dispatch

    rejected_request = ProviderRequestError('omlx', 400)
    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher._execute', side_effect=rejected_request):
        with pytest.raises(ProviderRequestError) as error:
            dispatch(
                user_id='harald', provider_id='omlx', model='devstral',
                messages=[{'role': 'user', 'content': 'test'}], max_tokens=16,
            )

    assert error.value.status_code == 400


def test_dispatch_propagates_provider_request_errors_from_fallback(app):
    """A fallback validation error must not be queued or rewritten as a 503."""
    from dispatcher import ProviderRequestError, dispatch

    rejected_request = ProviderRequestError('openai', 400)
    with patch('dispatcher.health_tracker.is_healthy', return_value=False), \
         patch('dispatcher._execute', side_effect=rejected_request):
        with pytest.raises(ProviderRequestError) as error:
            dispatch(
                user_id='harald', provider_id='omlx', model='devstral',
                messages=[{'role': 'user', 'content': 'test'}], max_tokens=16,
                fallback_provider_override='openai',
            )

    assert error.value.provider_id == 'openai'


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


def test_dispatch_retries_fallback_on_429_rate_limit(app):
    """429 vom Primary-Provider löst Fallback aus (statt ProviderRequestError zu werfen)."""
    from dispatcher import ProviderRequestError, dispatch

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher._execute') as mock_exec:
        # Erster Aufruf: 429 (Primary)
        # Zweiter Aufruf: Erfolg (Fallback)
        mock_exec.side_effect = [
            ProviderRequestError('ollama', 429),
            {'content': [{'text': 'ok'}], 'usage': {'input_tokens': 5, 'output_tokens': 3}},
        ]

        result = dispatch(
            user_id='user-1', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
            fallback_provider_override='claude',
            fallback_model_override='claude-haiku',
        )

    assert mock_exec.call_count == 2
    assert result['fallback_used'] is True
    assert result['via'] == 'claude'
    assert result['model'] == 'claude-haiku'


def test_dispatch_still_blocked_on_400_bad_request(app):
    """400 vom Primary wird NICHT gefallbackt (request-spezifischer Fehler)."""
    from dispatcher import ProviderRequestError, dispatch

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.side_effect = ProviderRequestError('ollama', 400)

        with pytest.raises(ProviderRequestError) as error:
            dispatch(
                user_id='user-1', provider_id='ollama', model='qwen:latest',
                messages=[{'role': 'user', 'content': 'hi'}],
                fallback_provider_override='claude',
            )

    assert error.value.status_code == 400
    assert mock_exec.call_count == 1  # Nur Primary probiert


def test_dispatch_queues_when_both_primary_and_fallback_return_429(app):
    """Wenn Primary + Fallback beide 429 geben, wird gequeued (oder ProviderUnavailableError)."""
    from dispatcher import ProviderRequestError, dispatch, ProviderUnavailableError
    from storage.models import ProviderConfig
    from database import db

    # ProviderConfig mit queue, aber ohne fallback (Default-Werte reichen)
    pc = ProviderConfig(
        user_id='user-6', provider_id='ollama',
        queue_when_unavailable=True, queue_ttl_hours=24,
    )
    pc.set_config({})
    db.session.add(pc)
    db.session.commit()

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.side_effect = ProviderRequestError('ollama', 429)

        result = dispatch(
            user_id='user-6', provider_id='ollama', model='qwen:latest',
            messages=[{'role': 'user', 'content': 'hi'}],
            # Kein Fallback-Override — fällt auf DB-Config (die keinen fallback hat)
        )

    assert result.get('queued') is True
    assert 'queue_id' in result


def test_dispatch_fallback_429_no_queue_raises_error(app):
    """Wenn Primary + Fallback 429 geben und kein Queue konfiguriert -> ProviderUnavailableError."""
    from dispatcher import ProviderRequestError, dispatch, ProviderUnavailableError

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher._execute') as mock_exec:
        mock_exec.side_effect = ProviderRequestError('ollama', 429)

        with pytest.raises(ProviderUnavailableError):
            dispatch(
                user_id='user-7', provider_id='ollama', model='qwen:latest',
                messages=[{'role': 'user', 'content': 'hi'}],
                fallback_provider_override='openai',
            )

