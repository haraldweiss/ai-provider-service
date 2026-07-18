# SPDX-License-Identifier: AGPL-3.0-or-later
"""Colibri system-provider access and dispatch protections."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from database import db
from storage.models import ProviderConfig


def _fake_provider_stream(events=(), result=None):
    from providers.base import ProviderStream

    return ProviderStream(
        events=iter(events),
        close=Mock(),
        result=result or {
            'content': [{'text': 'Hallo'}],
            'usage': {'input_tokens': 1, 'output_tokens': 1},
            'stop_reason': 'stop',
        },
    )


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


def test_dispatch_stream_opens_colibri_before_response_headers(app, monkeypatch):
    """Native streaming admits Colibri before Flask can commit headers."""
    from dispatcher import dispatch_stream

    upstream_stream = _fake_provider_stream()
    opened = Mock(return_value=upstream_stream)
    monkeypatch.setattr('dispatcher._load_config', lambda *_: {})
    monkeypatch.setattr(
        'dispatcher.get_client', lambda *_: SimpleNamespace(open_stream=opened),
    )
    monkeypatch.setattr('dispatcher.health_tracker.is_healthy', lambda _: True)

    stream = dispatch_stream(
        'harald', 'colibri', 'glm-5.2-colibri', [], 16,
        tools=[], tool_choice='auto',
    )

    assert stream is upstream_stream
    opened.assert_called_once_with(
        'glm-5.2-colibri', [], 16, tools=[], tool_choice='auto',
    )


def test_dispatch_stream_records_one_success_and_closes(app, monkeypatch):
    """A complete native stream is recorded once after its final frame."""
    from dispatcher import dispatch_stream

    upstream_stream = _fake_provider_stream(events=[{'choices': []}])
    opened = Mock(return_value=upstream_stream)
    usage = Mock()
    audit = Mock()
    healthy = Mock()
    monkeypatch.setattr('dispatcher._load_config', lambda *_: {})
    monkeypatch.setattr(
        'dispatcher.get_client', lambda *_: SimpleNamespace(open_stream=opened),
    )
    monkeypatch.setattr('dispatcher.health_tracker.is_healthy', lambda _: True)
    monkeypatch.setattr('dispatcher._log_usage_event', usage)
    monkeypatch.setattr('dispatcher._write_audit_note', audit)
    monkeypatch.setattr('dispatcher.health_tracker.set_status', healthy)

    stream = dispatch_stream(
        'harald', 'colibri', 'glm-5.2-colibri', [], 16,
        tools=[], tool_choice=None,
    )
    assert list(stream.events) == [{'choices': []}]

    usage.assert_called_once_with(
        'harald', 'colibri', 'glm-5.2-colibri', 1, 1, 'success',
        origin_app=None,
    )
    audit.assert_called_once()
    healthy.assert_called_once_with('colibri', True)
    upstream_stream.close.assert_called_once()


def test_dispatch_stream_records_one_error_and_closes(app, monkeypatch):
    """Parser/network failures do not queue or retry a native stream."""
    from dispatcher import dispatch_stream

    def failing_events():
        raise RuntimeError('upstream contained secret prompt text')
        yield  # pragma: no cover

    upstream_stream = _fake_provider_stream(events=failing_events())
    opened = Mock(return_value=upstream_stream)
    usage = Mock()
    unhealthy = Mock()
    monkeypatch.setattr('dispatcher._load_config', lambda *_: {})
    monkeypatch.setattr(
        'dispatcher.get_client', lambda *_: SimpleNamespace(open_stream=opened),
    )
    monkeypatch.setattr('dispatcher.health_tracker.is_healthy', lambda _: True)
    monkeypatch.setattr('dispatcher._log_usage_event', usage)
    monkeypatch.setattr('dispatcher.health_tracker.set_status', unhealthy)

    stream = dispatch_stream(
        'harald', 'colibri', 'glm-5.2-colibri', [], 16,
        tools=[], tool_choice=None,
    )
    with pytest.raises(RuntimeError, match='secret prompt text'):
        list(stream.events)

    usage.assert_called_once_with(
        'harald', 'colibri', 'glm-5.2-colibri', None, None, 'error',
        error_message='RuntimeError', origin_app=None,
    )
    unhealthy.assert_called_once_with(
        'colibri', False, reason='RuntimeError', persistent=False,
    )
    upstream_stream.close.assert_called_once()
