"""WebDAV bridge tests — PROPFIND, GET, PUT."""

import pytest
from pathlib import Path
from config import Config


@pytest.fixture
def headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    p = tmp_path / 'vault'
    p.mkdir()
    monkeypatch.setattr(Config, 'VAULT_PATH', str(p))
    return p


def test_propfind_root(client, headers, vault_dir, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='dav',
                                       body='test', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
    r = client.open('/memory/dav/?user_id=harald', method='PROPFIND', headers=headers)
    assert r.status_code == 207
    assert '/bt/' in r.get_data(as_text=True)


def test_get_file(client, headers, vault_dir, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='dav',
                                       body='hello world', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
    r = client.get('/memory/dav/bt/notes/dav.md?user_id=harald', headers=headers)
    assert r.status_code == 200
    assert b'hello world' in r.data


def test_put_creates_file(client, headers, vault_dir, app):
    with app.app_context():
        path = vault_dir / 'harald' / 'bt' / 'notes' / 'put-test.md'
        assert not path.exists()
    r = client.put('/memory/dav/bt/notes/put-test.md?user_id=harald',
                     headers=headers, data='# PUT created\nbody')
    assert r.status_code == 204
    with app.app_context():
        path = vault_dir / 'harald' / 'bt' / 'notes' / 'put-test.md'
        assert path.exists()
        assert 'PUT created' in path.read_text()


def test_get_missing_returns_404(client, headers, vault_dir, app):
    r = client.get('/memory/dav/nonexistent.md?user_id=harald', headers=headers)
    assert r.status_code == 404


def test_path_traversal_rejected(client, headers, vault_dir, app):
    r = client.get('/memory/dav/../../../etc/passwd?user_id=harald', headers=headers)
    assert r.status_code == 404


def test_requires_auth(client):
    r = client.get('/memory/dav/bt/notes/x.md')
    assert r.status_code == 401
