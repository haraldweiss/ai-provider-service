"""WebDAV bridge tests — PROPFIND, GET, PUT."""

import pytest
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


# --- Basic-Auth support (so Obsidian Remotely Save / macOS Finder can mount) ---


@pytest.fixture
def basic_headers():
    """Basic Auth: user=harald, password=SERVICE_TOKEN."""
    import base64
    raw = f'harald:{Config.SERVICE_TOKEN}'.encode('utf-8')
    encoded = base64.b64encode(raw).decode('ascii')
    return {'Authorization': f'Basic {encoded}'}


def test_propfind_with_basic_auth(client, basic_headers, vault_dir, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='dav',
                                       body='test', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
    r = client.open('/memory/dav/', method='PROPFIND', headers=basic_headers)
    assert r.status_code == 207
    assert '/bt/' in r.get_data(as_text=True)


def test_get_with_basic_auth(client, basic_headers, vault_dir, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='hi',
                                       body='hello world', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
    r = client.get('/memory/dav/bt/notes/hi.md', headers=basic_headers)
    assert r.status_code == 200
    assert b'hello world' in r.data


def test_put_with_basic_auth_writes_under_basic_user(client, basic_headers, vault_dir, app):
    """Basic Auth username determines user scope — NOT the query string."""
    r = client.put('/memory/dav/bt/notes/from-basic.md',
                    headers=basic_headers, data='# via basic\n')
    assert r.status_code == 204
    path = vault_dir / 'harald' / 'bt' / 'notes' / 'from-basic.md'
    assert path.exists()


def test_basic_auth_wrong_password_rejected(client, vault_dir):
    import base64
    raw = b'harald:wrong-token'
    bad = {'Authorization': f'Basic {base64.b64encode(raw).decode("ascii")}'}
    r = client.open('/memory/dav/', method='PROPFIND', headers=bad)
    assert r.status_code == 401
    # Must offer Basic challenge so clients show the auth dialog
    assert 'Basic' in r.headers.get('WWW-Authenticate', '')


def test_basic_auth_malformed_header_rejected(client, vault_dir):
    bad = {'Authorization': 'Basic not-base64-!!!'}
    r = client.open('/memory/dav/', method='PROPFIND', headers=bad)
    assert r.status_code == 401


def test_no_auth_returns_basic_challenge(client, vault_dir):
    """Unauthenticated WebDAV requests must include WWW-Authenticate: Basic so
    Finder / Remotely Save show a login prompt instead of failing silently."""
    r = client.open('/memory/dav/', method='PROPFIND')
    assert r.status_code == 401
    assert 'Basic' in r.headers.get('WWW-Authenticate', '')


def test_bearer_still_works_after_basic_patch(client, headers, vault_dir, app):
    """Regression — existing Bearer-based callers must keep working."""
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='still',
                                       body='works', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
    r = client.open('/memory/dav/?user_id=harald', method='PROPFIND', headers=headers)
    assert r.status_code == 207


def test_basic_auth_not_accepted_on_non_dav_routes(client, vault_dir, app):
    """Security: Basic Auth must NOT widen the auth surface beyond WebDAV.
    /memory/notes still requires Bearer."""
    import base64
    raw = f'harald:{Config.SERVICE_TOKEN}'.encode('utf-8')
    encoded = base64.b64encode(raw).decode('ascii')
    basic_only = {'Authorization': f'Basic {encoded}'}
    r = client.get('/memory/notes?user_id=harald', headers=basic_only)
    assert r.status_code == 401


# --- OPTIONS handler (WebDAV capability discovery so clients don't bail) ---


def _allow_set(response):
    """Parse Allow header into a set of uppercase method names."""
    raw = response.headers.get('Allow', '')
    return {m.strip().upper() for m in raw.split(',') if m.strip()}


def test_options_root_advertises_all_methods(client, vault_dir):
    """The OPTIONS response on the WebDAV root MUST list every method the
    bridge actually supports — otherwise WebDAV clients like Remotely Save
    bail out during capability discovery."""
    r = client.open('/memory/dav/', method='OPTIONS')
    assert r.status_code == 200
    allow = _allow_set(r)
    # Required by Obsidian Remotely Save and macOS Finder during discovery.
    for m in ('OPTIONS', 'PROPFIND', 'GET', 'PUT', 'MKCOL'):
        assert m in allow, f'OPTIONS Allow header missing {m} — got {allow}'


def test_options_subpath_advertises_all_methods(client, vault_dir):
    r = client.open('/memory/dav/anything.md', method='OPTIONS')
    assert r.status_code == 200
    allow = _allow_set(r)
    for m in ('OPTIONS', 'PROPFIND', 'GET', 'PUT', 'MKCOL'):
        assert m in allow


def test_options_advertises_dav_class_1_and_2(client, vault_dir):
    """WebDAV clients sniff the `DAV:` header to decide which protocol level
    is supported. RFC 4918 §10.1 — class 1 = basic, class 2 = locking. We
    don't lock, so '1' would be minimal, but stating '1, 2' is the canonical
    value most clients expect and harmlessly ignored if unused."""
    r = client.open('/memory/dav/', method='OPTIONS')
    dav = r.headers.get('DAV', '')
    assert '1' in dav, f'DAV header missing class 1 — got {dav!r}'


def test_options_does_not_require_auth(client, vault_dir):
    """OPTIONS must work without credentials so CORS / WebDAV preflight
    succeeds before the client has a chance to attach Basic Auth."""
    r = client.open('/memory/dav/', method='OPTIONS')
    assert r.status_code == 200
    r2 = client.open('/memory/dav/notes/x.md', method='OPTIONS')
    assert r2.status_code == 200


def test_options_with_basic_auth_still_ok(client, vault_dir):
    """Auth-equipped OPTIONS must not break — clients that always send Basic
    along should still get a clean 200."""
    import base64
    raw = f'harald:{Config.SERVICE_TOKEN}'.encode('utf-8')
    encoded = base64.b64encode(raw).decode('ascii')
    h = {'Authorization': f'Basic {encoded}'}
    r = client.open('/memory/dav/', method='OPTIONS', headers=h)
    assert r.status_code == 200
    allow = _allow_set(r)
    assert 'PROPFIND' in allow and 'PUT' in allow
