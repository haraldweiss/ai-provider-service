# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests: _execute schreibt UsageEvent bei success und error."""
from __future__ import annotations
import pytest
from unittest.mock import patch


def test_execute_logs_success_event(app):
    from dispatcher import _execute
    from storage.models import UsageEvent

    with patch('dispatcher.get_client') as mock_get_client:
        mock_client = mock_get_client.return_value
        mock_client.create_message.return_value = {
            'content': [{'text': 'hi'}],
            'usage': {'input_tokens': 42, 'output_tokens': 17},
        }
        _execute('user-1', 'ollama', 'llama3.1:8b',
                 [{'role': 'user', 'content': 'hi'}], 100)

    events = UsageEvent.query.all()
    assert len(events) == 1
    ev = events[0]
    assert ev.user_id == 'user-1'
    assert ev.provider_id == 'ollama'
    assert ev.model == 'llama3.1:8b'
    assert ev.input_tokens == 42
    assert ev.output_tokens == 17
    assert ev.cost_usd == 0.0
    assert ev.status == 'success'
    assert ev.error_message is None


def test_execute_logs_error_event(app):
    from dispatcher import _execute
    from storage.models import UsageEvent

    with patch('dispatcher.get_client') as mock_get_client:
        mock_client = mock_get_client.return_value
        mock_client.create_message.side_effect = RuntimeError('boom')

        with pytest.raises(RuntimeError):
            _execute('user-1', 'claude', 'claude-haiku-4-5',
                     [{'role': 'user', 'content': 'hi'}], 100)

    events = UsageEvent.query.all()
    assert len(events) == 1
    ev = events[0]
    assert ev.status == 'error'
    assert 'RuntimeError' in ev.error_message
    assert 'boom' in ev.error_message
    assert ev.input_tokens is None
    assert ev.cost_usd is None


def test_execute_with_origin_app(app):
    from dispatcher import _execute
    from storage.models import UsageEvent

    with patch('dispatcher.get_client') as mock_get_client:
        mock_client = mock_get_client.return_value
        mock_client.create_message.return_value = {
            'content': [{'text': 'x'}],
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }
        _execute('user-1', 'ollama', 'qwen',
                 [{'role': 'user', 'content': 'hi'}], 100,
                 origin_app='bewerbungstracker')

    ev = UsageEvent.query.one()
    assert ev.origin_app == 'bewerbungstracker'


def test_dispatch_passes_origin_app_through(app):
    """dispatch() reicht origin_app an _execute durch und das landet im Event."""
    from dispatcher import dispatch
    from storage.models import UsageEvent

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher.get_client') as mock_get_client:
        mock_client = mock_get_client.return_value
        mock_client.create_message.return_value = {
            'content': [{'text': 'ok'}],
            'usage': {'input_tokens': 5, 'output_tokens': 3},
        }

        dispatch(
            user_id='u1', provider_id='ollama', model='m',
            messages=[{'role': 'user', 'content': 'x'}],
            origin_app='loganonymizer',
        )

    ev = UsageEvent.query.one()
    assert ev.origin_app == 'loganonymizer'
