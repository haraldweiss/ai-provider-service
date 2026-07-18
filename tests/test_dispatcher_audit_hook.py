"""Audit hook in dispatcher._execute writes an audit note via MemoryWriter."""

import pytest
from unittest.mock import patch, MagicMock
from storage.memory_models import MemoryNote, MemoryKind


@pytest.fixture
def memory_enabled(monkeypatch):
    import config as _config_module
    monkeypatch.setattr(_config_module.Config, 'MEMORY_ENABLED', True)


def _build_messages():
    return [{'role': 'user', 'content': 'hi'}]


def test_successful_chat_writes_audit(app, memory_enabled):
    with app.app_context():
        fake_response = {
            'content': 'hello',
            'usage': {'input_tokens': 4, 'output_tokens': 2},
        }
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = fake_response
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    from dispatcher import _execute
                    _execute(user_id='harald', provider_id='claude',
                             model='claude-haiku', messages=_build_messages(),
                             max_tokens=100, origin_app='bewerbungstracker')

        audits = MemoryNote.query.filter_by(kind=MemoryKind.AUDIT).all()
        assert len(audits) == 1
        a = audits[0]
        assert a.user_id == 'harald'
        assert a.app == 'bewerbungstracker'
        assert a.extra['provider'] == 'claude'
        assert '## Prompt' in a.body
        assert '## Response' in a.body


def test_failed_chat_does_not_write_audit(app, memory_enabled):
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.side_effect = RuntimeError('boom')
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    from dispatcher import _execute
                    with pytest.raises(RuntimeError):
                        _execute(user_id='u', provider_id='claude', model='m',
                                 messages=_build_messages(), max_tokens=10,
                                 origin_app='bt')
        assert MemoryNote.query.filter_by(kind=MemoryKind.AUDIT).count() == 0


def test_memory_disabled_skips_audit(app):
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = {'content': 'ok', 'usage': {}}
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    from dispatcher import _execute
                    _execute(user_id='u', provider_id='claude', model='m',
                             messages=_build_messages(), max_tokens=10,
                             origin_app='bt')
        assert MemoryNote.query.filter_by(kind=MemoryKind.AUDIT).count() == 0


def test_audit_failure_does_not_break_chat(app, memory_enabled):
    """If MemoryWriter raises, _execute must still return the model response."""
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = {'content': 'ok', 'usage': {}}
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    with patch('dispatcher._write_audit_note',
                               side_effect=RuntimeError('db full')):
                        from dispatcher import _execute
                        result = _execute(user_id='u', provider_id='claude',
                                          model='m', messages=_build_messages(),
                                          max_tokens=10, origin_app='bt')
                        assert result['content'] == 'ok'
