# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für UsageEvent-Model: Insert + Filter-Query."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from database import db


def test_usage_event_insert_and_query(app):
    from storage.models import UsageEvent
    e = UsageEvent(
        user_id='user-1', provider_id='ollama', model='llama3.1:8b',
        input_tokens=120, output_tokens=80, cost_usd=0.0,
        origin_app=None, status='success',
    )
    db.session.add(e)
    db.session.commit()
    rows = UsageEvent.query.filter_by(user_id='user-1').all()
    assert len(rows) == 1
    assert rows[0].provider_id == 'ollama'
    assert rows[0].input_tokens == 120
    assert rows[0].status == 'success'
    assert rows[0].created_at is not None


def test_usage_event_error_row(app):
    from storage.models import UsageEvent
    e = UsageEvent(
        user_id='user-1', provider_id='claude', model='claude-haiku-4-5',
        input_tokens=None, output_tokens=None, cost_usd=None,
        status='error', error_message='ConnectionError: timeout',
    )
    db.session.add(e)
    db.session.commit()
    row = UsageEvent.query.filter_by(status='error').one()
    assert row.error_message.startswith('ConnectionError')
    assert row.input_tokens is None
    assert row.cost_usd is None


def test_usage_event_since_filter(app):
    from storage.models import UsageEvent
    old = UsageEvent(user_id='u', provider_id='ollama', model='m',
                    status='success', created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    new = UsageEvent(user_id='u', provider_id='ollama', model='m',
                    status='success', created_at=datetime.now(timezone.utc))
    db.session.add_all([old, new])
    db.session.commit()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    rows = UsageEvent.query.filter(UsageEvent.created_at > cutoff).all()
    assert len(rows) == 1
