# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für GET /usage/events."""
from __future__ import annotations
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app import create_app
from database import db


@pytest.fixture
def app():
    os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
    os.environ['ENCRYPTION_KEY'] = 'X' * 44
    os.environ['SERVICE_TOKEN'] = 'test-token'
    app = create_app()
    app.config['TESTING'] = True
    # Config liest SERVICE_TOKEN beim Import (load_dotenv im Modul-Top-Level)
    # und ignoriert nachträgliches os.environ — daher explizit überschreiben.
    from config import Config
    original_token = Config.SERVICE_TOKEN
    Config.SERVICE_TOKEN = 'test-token'
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()
    Config.SERVICE_TOKEN = original_token


@pytest.fixture
def client(app):
    return app.test_client()


def _seed(n: int, user_id='u1'):
    from storage.models import UsageEvent
    base = datetime(2026, 5, 1, 12, 0, 0)
    for i in range(n):
        ev = UsageEvent(
            user_id=user_id, provider_id='ollama', model='m',
            input_tokens=10, output_tokens=5, cost_usd=0.0,
            status='success',
        )
        ev.created_at = base + timedelta(minutes=i)
        db.session.add(ev)
    db.session.commit()


def test_requires_auth(app, client):
    with app.app_context():
        _seed(3)
    res = client.get('/usage/events?user_id=u1')
    assert res.status_code == 401


def test_requires_user_id(app, client):
    res = client.get('/usage/events',
                     headers={'Authorization': 'Bearer test-token'})
    assert res.status_code == 400


def test_returns_events_for_user(app, client):
    with app.app_context():
        _seed(3, user_id='u1')
        _seed(2, user_id='u2')
    res = client.get('/usage/events?user_id=u1',
                     headers={'Authorization': 'Bearer test-token'})
    assert res.status_code == 200
    data = res.get_json()
    assert data['count'] == 3
    assert len(data['events']) == 3
    assert all(e['user_id'] == 'u1' for e in data['events'])
    assert data['has_more'] is False


def test_since_filter(app, client):
    with app.app_context():
        _seed(5)
    res = client.get(
        '/usage/events?user_id=u1&since=2026-05-01T12:01:30',
        headers={'Authorization': 'Bearer test-token'},
    )
    data = res.get_json()
    # 3 events nach 12:01:30 (12:02, 12:03, 12:04)
    assert data['count'] == 3


def test_pagination_limit(app, client):
    with app.app_context():
        _seed(10)
    res = client.get('/usage/events?user_id=u1&limit=4',
                     headers={'Authorization': 'Bearer test-token'})
    data = res.get_json()
    assert data['count'] == 4
    assert data['has_more'] is True
    assert data['next_since'] is not None
    res2 = client.get(
        f'/usage/events?user_id=u1&since={data["next_since"]}&limit=4',
        headers={'Authorization': 'Bearer test-token'},
    )
    data2 = res2.get_json()
    assert data2['count'] == 4
    assert data2['has_more'] is True


def test_invalid_since_returns_400(app, client):
    res = client.get('/usage/events?user_id=u1&since=not-a-timestamp',
                     headers={'Authorization': 'Bearer test-token'})
    assert res.status_code == 400
