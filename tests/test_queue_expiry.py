# SPDX-License-Identifier: AGPL-3.0-or-later
"""Orphaned queue rows must not linger forever (2026-11-18).

Queue drains only run on a provider health down->up transition
(``health_tracker.just_recovered``), so rows belonging to a provider that never
flips stay put. Clients poll a queued item for only a few seconds, so rows past
their TTL are orphans and only the expiry sweep can retire them.

The original sweep looked at ``status='pending'`` only, leaving a crashed
``processing`` row from 2026-05-15 stuck in production indefinitely.
"""

from datetime import datetime, timedelta, timezone

from database import db
from dispatcher import expire_stale_queue_items
from storage.models import RequestQueue


def _row(app, status, expires_in_minutes, provider='ollama'):
    with app.app_context():
        q = RequestQueue(
            user_id='wolfini_de_web',
            primary_provider=provider,
            payload='{"provider": "ollama", "model": "mistral-nemo-cc:latest"}',
            status=status,
            attempts=0,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes),
        )
        db.session.add(q)
        db.session.commit()
        return q.id


def _status(app, row_id):
    with app.app_context():
        return db.session.get(RequestQueue, row_id).status


def test_expires_orphaned_pending_row(app):
    row_id = _row(app, 'pending', -5)
    with app.app_context():
        assert expire_stale_queue_items() == 1
    assert _status(app, row_id) == 'expired'


def test_expires_crashed_processing_row(app):
    """RED before the fix: an expired 'processing' row was never retired."""
    row_id = _row(app, 'processing', -5)
    with app.app_context():
        assert expire_stale_queue_items() == 1
    assert _status(app, row_id) == 'expired'


def test_leaves_live_rows_untouched(app):
    pending_id = _row(app, 'pending', 30)
    processing_id = _row(app, 'processing', 30)
    expired_id = _row(app, 'expired', -30)
    done_id = _row(app, 'done', -30)

    with app.app_context():
        assert expire_stale_queue_items() == 0

    assert _status(app, pending_id) == 'pending'
    assert _status(app, processing_id) == 'processing'
    assert _status(app, expired_id) == 'expired'
    assert _status(app, done_id) == 'done'


def test_sweep_is_idempotent(app):
    _row(app, 'processing', -5)
    with app.app_context():
        assert expire_stale_queue_items() == 1
        assert expire_stale_queue_items() == 0


def test_worker_drain_sweeps_even_without_recovery(app, monkeypatch):
    """The periodic drain must clean up before looking at provider recovery."""
    import worker as w

    _row(app, 'processing', -5)
    calls = []
    monkeypatch.setattr(w, 'expire_stale_queue_items',
                        lambda *a, **k: calls.append('swept') or 1)
    monkeypatch.setattr(w.health_tracker, 'just_recovered', lambda pid: False)

    w._drain(app)

    assert calls == ['swept']
