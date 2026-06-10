"""Vault export API — tarball + single-file download with path-traversal guard."""

import io
import os
import tarfile
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


def test_vault_tarball_contains_only_user_subtree(client, headers, vault_dir, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='note',
                                      body='hi', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
        foreign = MemoryWriter().write_note(user_id='alice', app='bt', title='nope',
                                            body='', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(foreign)

    r = client.get('/memory/vault.tar.gz?user_id=harald', headers=headers)
    assert r.status_code == 200
    assert r.headers['Content-Type'] == 'application/gzip'

    tar_bytes = io.BytesIO(r.data)
    with tarfile.open(fileobj=tar_bytes, mode='r:gz') as t:
        names = t.getnames()
    assert all(n == 'harald' or n.startswith('harald/') for n in names if n)
    assert not any('alice' in n for n in names)


def test_vault_single_file(client, headers, vault_dir, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='file',
                                      body='content', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
    r = client.get('/memory/vault/bt/notes/file.md?user_id=harald', headers=headers)
    assert r.status_code == 200
    assert b'content' in r.data


def test_vault_path_traversal_rejected(client, headers, vault_dir, app):
    r = client.get('/memory/vault/../../../etc/passwd?user_id=harald', headers=headers)
    assert r.status_code in (400, 404)


def test_vault_absolute_path_rejected(client, headers, vault_dir, app):
    r = client.get('/memory/vault//etc/passwd?user_id=harald', headers=headers)
    assert r.status_code in (308, 400, 404)


def test_vault_missing_file_404(client, headers, vault_dir, app):
    r = client.get('/memory/vault/nothing/here.md?user_id=harald', headers=headers)
    assert r.status_code == 404


def test_vault_tarball_self_heals_when_disk_missing_db_notes(client, headers, vault_dir, app):
    """If DB has notes but on-disk vault is empty (ephemeral VAULT_PATH lost on
    container restart), the tarball endpoint must rebuild the user subtree before
    packing — otherwise the export is silently empty."""
    with app.app_context():
        from storage.memory import MemoryWriter
        n1 = MemoryWriter().write_note(user_id='harald', app='bt', title='one',
                                       body='body1', tags=[], folder=None, slug=None)
        n2 = MemoryWriter().write_note(user_id='harald', app='bt', title='two',
                                       body='body2', tags=[], folder=None, slug=None)
        # Deliberately NOT calling VaultRenderer — simulates the silent-failure
        # path where notes are in DB but never landed on disk.
        assert n1.id and n2.id

    assert not (vault_dir / 'harald').exists()

    r = client.get('/memory/vault.tar.gz?user_id=harald', headers=headers)
    assert r.status_code == 200

    tar_bytes = io.BytesIO(r.data)
    with tarfile.open(fileobj=tar_bytes, mode='r:gz') as t:
        md_names = [n for n in t.getnames() if n.endswith('.md')]
    assert len(md_names) == 2, f'self-heal expected 2 md files, got: {md_names}'
