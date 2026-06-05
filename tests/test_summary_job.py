"""SummaryJob runner — nightly per-day and per-app aggregates."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind, SummaryJob


@pytest.fixture
def free_models(monkeypatch):
    monkeypatch.setattr(Config, 'MEMORY_FREE_MODELS', ['ollama::mistral'])


def _seed_audit(user_id, app_name, when, n=3):
    from storage.memory import MemoryWriter
    from storage.memory_models import MemoryNote
    w = MemoryWriter()
    for i in range(n):
        note = w.write_audit(user_id=user_id, app=app_name, provider='claude',
                             chat_request_id=f'{user_id}-{app_name}-{when.date()}-{i}',
                             prompt=f'p{i}', response=f'r{i}',
                             tokens={}, cost_eur=0, latency_ms=10, timestamp=when)
        note.created_at = when
        db.session.commit()


def test_run_for_day_creates_summary(app, free_models):
    with app.app_context():
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        _seed_audit('harald', 'bt', yesterday, n=2)
        with patch('agents.summary_job._call_model',
                   return_value=('Yesterday harald had 2 audits.', 'ollama::mistral')):
            from agents.summary_job import run_for_day
            jobs = run_for_day(yesterday.date())
        assert jobs[0].status == 'completed'
        summaries = MemoryNote.query.filter_by(kind=MemoryKind.SUMMARY,
                                                user_id='harald').all()
        assert len(summaries) >= 1


def test_run_for_day_skips_user_with_no_audit(app, free_models):
    with app.app_context():
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        from agents.summary_job import run_for_day
        jobs = run_for_day(yesterday)
        assert jobs == []


def test_run_for_day_handles_llm_failure(app, free_models):
    with app.app_context():
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        _seed_audit('u1', 'bt', yesterday, n=1)
        with patch('agents.summary_job._call_model',
                   side_effect=RuntimeError('all models failed')):
            from agents.summary_job import run_for_day
            jobs = run_for_day(yesterday.date())
        assert jobs[0].status == 'failed'
        assert 'failed' in jobs[0].error_msg.lower()


def test_run_for_day_respects_max_notes_cap(app, free_models, monkeypatch):
    monkeypatch.setattr(Config, 'SUMMARY_MAX_NOTES_PER_DAY', 2)
    with app.app_context():
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        _seed_audit('u', 'bt', yesterday, n=5)
        with patch('agents.summary_job._call_model') as mock:
            from agents.summary_job import run_for_day
            jobs = run_for_day(yesterday.date())
        assert mock.call_count == 0
        assert jobs[0].status == 'completed'
        sums = MemoryNote.query.filter_by(kind=MemoryKind.SUMMARY, user_id='u').all()
        assert any('skipped' in (s.body or '').lower() for s in sums)
