"""ORM models for memory notes and summary jobs."""

import pytest
from database import db
from storage.memory_models import MemoryNote, SummaryJob, MemoryKind


def test_create_note(app):
    with app.app_context():
        n = MemoryNote(
            user_id='harald', app='bewerbungstracker', kind=MemoryKind.NOTE,
            folder='notes', slug='meeting-acme',
            title='Meeting with Acme', body='Discussed terms.',
            tags=['meetings'], extra={},
        )
        db.session.add(n)
        db.session.commit()
        assert n.id is not None
        assert n.created_at is not None


def test_unique_constraint(app):
    with app.app_context():
        n1 = MemoryNote(user_id='harald', app='bt', kind=MemoryKind.NOTE,
                        folder='notes', slug='dup', title='A', body='', tags=[], extra={})
        db.session.add(n1)
        db.session.commit()
        n2 = MemoryNote(user_id='harald', app='bt', kind=MemoryKind.NOTE,
                        folder='notes', slug='dup', title='B', body='', tags=[], extra={})
        db.session.add(n2)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


def test_chat_request_id_unique_partial(app):
    """audit notes with same chat_request_id must collide; null chat_request_id
    must NOT collide."""
    with app.app_context():
        a1 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.AUDIT,
                        folder='audit', slug='a', title='', body='',
                        tags=[], extra={'chat_request_id': 'req-1'},
                        chat_request_id='req-1')
        a2 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.AUDIT,
                        folder='audit', slug='b', title='', body='',
                        tags=[], extra={'chat_request_id': 'req-1'},
                        chat_request_id='req-1')
        db.session.add(a1)
        db.session.commit()
        db.session.add(a2)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

        b1 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.NOTE,
                        folder='notes', slug='x', title='', body='',
                        tags=[], extra={})
        b2 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.NOTE,
                        folder='notes', slug='y', title='', body='',
                        tags=[], extra={})
        db.session.add(b1)
        db.session.add(b2)
        db.session.commit()


def test_soft_delete_default_null(app):
    with app.app_context():
        n = MemoryNote(user_id='u', app='a', kind=MemoryKind.NOTE,
                       folder='notes', slug='s', title='t', body='', tags=[], extra={})
        db.session.add(n)
        db.session.commit()
        assert n.deleted_at is None


def test_summary_job_create(app):
    with app.app_context():
        j = SummaryJob(period='day:2026-06-05', user_id='harald', status='pending')
        db.session.add(j)
        db.session.commit()
        assert j.id is not None
        assert j.status == 'pending'
