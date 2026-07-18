"""MemoryWriter — DB-only insert path. Filesystem render is tested separately."""

import pytest
from storage.memory import MemoryWriter, NoteAlreadyExists
from storage.memory_models import MemoryKind


def test_write_note_minimal(app):
    with app.app_context():
        w = MemoryWriter()
        note = w.write_note(user_id='harald', app='bt',
                            title='Hello', body='World', tags=[], folder=None, slug=None)
        assert note.id is not None
        assert note.kind == MemoryKind.NOTE
        assert note.folder == 'bt/notes'
        assert note.slug == 'hello'


def test_write_note_slug_collision_autosuffix(app):
    with app.app_context():
        w = MemoryWriter()
        w.write_note(user_id='u', app='a', title='Same Title', body='', tags=[], folder=None, slug=None)
        n2 = w.write_note(user_id='u', app='a', title='Same Title', body='', tags=[], folder=None, slug=None)
        assert n2.slug == 'same-title-2'


def test_write_note_shared_folder(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_note(user_id='u', app='a', title='Cross', body='', tags=[],
                         folder='_shared', slug=None)
        assert n.folder == '_shared/notes'


def test_write_note_explicit_slug(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_note(user_id='u', app='a', title='X', body='', tags=[],
                         folder=None, slug='custom-slug')
        assert n.slug == 'custom-slug'


def test_write_note_invalid_explicit_slug_raises(app):
    with app.app_context():
        w = MemoryWriter()
        with pytest.raises(ValueError, match='invalid slug'):
            w.write_note(user_id='u', app='a', title='X', body='', tags=[],
                         folder=None, slug='Bad Slug!')


def test_write_audit_basic(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_audit(
            user_id='harald', app='bt', provider='claude',
            chat_request_id='req-abc12',
            prompt='hi', response='hello',
            tokens={'prompt': 5, 'completion': 7},
            cost_eur=0.0001, latency_ms=120,
            timestamp=None,
        )
        assert n.kind == MemoryKind.AUDIT
        assert n.folder.startswith('bt/audit/')
        assert n.chat_request_id == 'req-abc12'
        assert n.extra['provider'] == 'claude'
        assert '## Prompt' in n.body
        assert '## Response' in n.body


def test_write_audit_idempotent_on_duplicate_request_id(app):
    with app.app_context():
        w = MemoryWriter()
        w.write_audit(user_id='u', app='a', provider='p', chat_request_id='req-x',
                      prompt='', response='', tokens={}, cost_eur=0, latency_ms=0,
                      timestamp=None)
        with pytest.raises(NoteAlreadyExists):
            w.write_audit(user_id='u', app='a', provider='p', chat_request_id='req-x',
                          prompt='different', response='', tokens={}, cost_eur=0,
                          latency_ms=0, timestamp=None)


def test_write_event(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_event(user_id='u', app='bt',
                          event_type='application_created',
                          payload={'company': 'ACME', 'position': 'Engineer'},
                          tags=['jobs'], slug=None)
        assert n.kind == MemoryKind.EVENT
        assert n.folder == 'bt/events/application_created'
        assert n.extra['event_type'] == 'application_created'
        assert n.extra['payload']['company'] == 'ACME'


def test_write_summary(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_summary(user_id='u', period='day:2026-06-05',
                            body='Summary text', source_ids=[1, 2, 3],
                            model_used='opencode::deepseek-v4-flash-free')
        assert n.kind == MemoryKind.SUMMARY
        assert n.folder == '_index/by-day'
        assert n.slug == '2026-06-05'
        assert n.extra['source_ids'] == [1, 2, 3]
        assert n.extra['model'] == 'opencode::deepseek-v4-flash-free'
