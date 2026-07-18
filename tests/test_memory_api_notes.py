"""Memory API — notes CRUD with auth scoping."""

import pytest
from config import Config
from storage.memory_models import MemoryNote


@pytest.fixture
def user_headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_create_note_returns_201(client, user_headers):
    r = client.post('/memory/notes',
                    headers=user_headers,
                    json={'user_id': 'harald', 'app': 'bt',
                          'title': 'Hello', 'body': 'World', 'tags': ['x']})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert 'id' in body
    assert body['path'].endswith('hello.md')


def test_list_notes_scoped_to_user(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        MemoryWriter().write_note(user_id='harald', app='bt', title='A',
                                  body='', tags=[], folder=None, slug=None)
        MemoryWriter().write_note(user_id='alice', app='bt', title='B',
                                  body='', tags=[], folder=None, slug=None)
    r = client.get('/memory/notes?user_id=harald', headers=user_headers)
    assert r.status_code == 200
    notes = r.get_json()['notes']
    titles = sorted(n['title'] for n in notes)
    assert titles == ['A']


def test_get_single_note(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='X',
                                      body='hi', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.get(f'/memory/notes/{nid}?user_id=harald', headers=user_headers)
    assert r.status_code == 200
    assert r.get_json()['title'] == 'X'


def test_get_other_users_note_returns_404(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='alice', app='bt', title='Y',
                                      body='', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.get(f'/memory/notes/{nid}?user_id=harald', headers=user_headers)
    assert r.status_code == 404


def test_patch_only_editable_kinds(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='X',
                                      body='old', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.patch(f'/memory/notes/{nid}?user_id=harald',
                     headers=user_headers, json={'body': 'new'})
    assert r.status_code == 200
    assert r.get_json()['body'] == 'new'


def test_patch_audit_rejected(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_audit(
            user_id='harald', app='bt', provider='claude',
            chat_request_id='r1', prompt='', response='', tokens={},
            cost_eur=0, latency_ms=0, timestamp=None)
        nid = n.id
    r = client.patch(f'/memory/notes/{nid}?user_id=harald',
                     headers=user_headers, json={'body': 'tampering'})
    assert r.status_code == 403


def test_delete_soft_deletes(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='gone',
                                      body='', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.delete(f'/memory/notes/{nid}?user_id=harald', headers=user_headers)
    assert r.status_code == 204
    with app.app_context():
        assert MemoryNote.query.get(nid).deleted_at is not None


def test_create_note_requires_auth(client):
    r = client.post('/memory/notes', json={'user_id': 'x', 'app': 'a',
                                            'title': 't', 'body': '', 'tags': []})
    assert r.status_code == 401


def test_body_size_limit(client, user_headers):
    big = 'x' * (1024 * 1024 + 10)
    r = client.post('/memory/notes', headers=user_headers,
                    json={'user_id': 'h', 'app': 'a', 'title': 'big',
                          'body': big, 'tags': []})
    assert r.status_code == 413
