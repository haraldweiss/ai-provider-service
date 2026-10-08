# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests für GET /usage/events."""
from __future__ import annotations
from datetime import datetime, timedelta
from database import db


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


def test_cursor_keeps_events_with_equal_timestamps(app, client):
    from storage.models import UsageEvent
    with app.app_context():
        _seed(5)
        UsageEvent.query.update({'created_at': datetime(2026, 5, 1, 12)})
        db.session.commit()
    headers = {'Authorization': 'Bearer test-token'}
    ids = []
    cursor = None
    for _ in range(3):
        query = {'user_id': 'u1', 'limit': 2}
        if cursor:
            query['cursor'] = cursor
        page = client.get('/usage/events', query_string=query, headers=headers).get_json()
        ids.extend(e['id'] for e in page['events'])
        cursor = page['next_cursor']
    assert len(ids) == len(set(ids)) == 5
    assert page['has_more'] is False


def test_full_last_page_does_not_claim_more(app, client):
    with app.app_context():
        _seed(2)
    page = client.get('/usage/events?user_id=u1&limit=2',
                      headers={'Authorization': 'Bearer test-token'}).get_json()
    assert page['has_more'] is False


def test_personal_token_only_discovers_own_identity(app, client):
    from storage.user_tokens import issue_user_token
    with app.app_context():
        mine = issue_user_token('lisa')
        issue_user_token('eve')
    result = client.get('/usage/users', headers={'Authorization': f'Bearer {mine}'})
    assert result.status_code == 200
    assert [u['user_id'] for u in result.get_json()['users']] == ['lisa']


def test_invalid_cursor_rejected(client):
    response = client.get('/usage/events?user_id=u1&cursor=bad',
                          headers={'Authorization': 'Bearer test-token'})
    assert response.status_code == 400


def test_cursor_rejects_empty_timestamp_and_overflow(client):
    for cursor in ('|1', '2026-05-01T12:00:00|999999999999999999999999'):
        response = client.get('/usage/events', query_string={'user_id': 'u1', 'cursor': cursor},
                              headers={'Authorization': 'Bearer test-token'})
        assert response.status_code == 400
