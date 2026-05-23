"""Tests that dispatcher.dispatch() forwards `tools` kwarg to the provider client."""
from __future__ import annotations
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['ENCRYPTION_KEY'] = 'X' * 44
os.environ['SERVICE_TOKEN'] = 'test-token'

from app import create_app
from database import db


@pytest.fixture
def app():
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    app = create_app()
    app.config['TESTING'] = True
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_dispatch_forwards_tools_to_provider(app):
    from dispatcher import dispatch

    fake_client = MagicMock()
    fake_client.create_message.return_value = {
        'content': [{'text': ''}],
        'stop_reason': 'tool_use',
        'tool_calls': [{'id': 't1', 'name': 'web_search', 'input': {'query': 'x'}}],
        'usage': {'input_tokens': 10, 'output_tokens': 5},
    }

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher.get_client', return_value=fake_client):
        out = dispatch('news-agent', 'claude', 'claude-sonnet-4-6',
                       [{'role': 'user', 'content': 'find news'}],
                       max_tokens=4096,
                       tools=[{'name': 'web_search', 'input_schema': {}}],
                       origin_app='news-agent')

    assert out['result']['stop_reason'] == 'tool_use'
    kwargs = fake_client.create_message.call_args.kwargs
    args = fake_client.create_message.call_args.args
    # tools may be passed as kwarg or positional; check kwarg path (our convention)
    assert kwargs.get('tools') is not None or (len(args) >= 4 and args[3] is not None), \
        "tools must be forwarded to the provider client"


def test_dispatch_without_tools_param_unchanged(app):
    """Backward compatibility: existing callers without tools still work."""
    from dispatcher import dispatch

    fake_client = MagicMock()
    fake_client.create_message.return_value = {
        'content': [{'text': 'hi'}],
        'usage': {'input_tokens': 5, 'output_tokens': 1},
    }

    with patch('dispatcher.health_tracker.is_healthy', return_value=True), \
         patch('dispatcher.get_client', return_value=fake_client):
        out = dispatch('user-1', 'claude', 'claude-haiku-4-5-20251001',
                       [{'role': 'user', 'content': 'hi'}])

    assert out['result']['content'] == [{'text': 'hi'}]
