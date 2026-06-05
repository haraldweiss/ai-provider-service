"""Memory API — events endpoints."""

import pytest
from config import Config


@pytest.fixture
def headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_create_event(client, headers):
    r = client.post('/memory/events', headers=headers,
                    json={'user_id': 'harald', 'app': 'bt',
                          'event_type': 'application_created',
                          'payload': {'company': 'ACME'}})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert 'application_created' in body['path']


def test_create_event_requires_type(client, headers):
    r = client.post('/memory/events', headers=headers,
                    json={'user_id': 'h', 'app': 'a', 'payload': {}})
    assert r.status_code == 400


def test_list_events_filter_by_type(client, headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        w = MemoryWriter()
        w.write_event(user_id='harald', app='bt', event_type='t1',
                      payload={}, tags=[], slug=None)
        w.write_event(user_id='harald', app='bt', event_type='t2',
                      payload={}, tags=[], slug=None)
    r = client.get('/memory/events?user_id=harald&event_type=t1', headers=headers)
    assert r.status_code == 200
    events = r.get_json()['events']
    assert len(events) == 1
    assert events[0]['extra']['event_type'] == 't1'
