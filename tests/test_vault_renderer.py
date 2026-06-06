"""VaultRenderer — projects DB rows onto markdown files under VAULT_PATH."""

import os
import pytest
from pathlib import Path
from database import db
from config import Config
from storage.memory import MemoryWriter
from storage.vault_renderer import VaultRenderer


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    p = tmp_path / 'vault'
    p.mkdir()
    monkeypatch.setattr(Config, 'VAULT_PATH', str(p))
    return p


def test_render_note_creates_file(app, vault_dir):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_note(user_id='harald', app='bt', title='Hello World',
                         body='Body text', tags=['x'], folder=None, slug=None)
        VaultRenderer().render_one(n)
        path = vault_dir / 'harald' / 'bt' / 'notes' / 'hello-world.md'
        assert path.exists()
        content = path.read_text()
        assert content.startswith('---\n')
        assert 'kind: "note"' in content
        assert 'user: "harald"' in content
        assert 'app: "bt"' in content
        assert 'Body text' in content


def test_render_audit_path_structure(app, vault_dir):
    with app.app_context():
        from datetime import datetime, timezone
        ts = datetime(2026, 6, 5, 14, 32, 11, tzinfo=timezone.utc)
        w = MemoryWriter()
        n = w.write_audit(user_id='u', app='bt', provider='claude',
                         chat_request_id='req-abc12345',
                         prompt='p', response='r', tokens={}, cost_eur=0.0,
                         latency_ms=10, timestamp=ts)
        VaultRenderer().render_one(n)
        expected = vault_dir / 'u' / 'bt' / 'audit' / '2026' / '06' / '05'
        files = list(expected.iterdir())
        assert len(files) == 1
        assert files[0].name.startswith('20260605T143211Z-req-abc12345')


def test_render_summary_in_index(app, vault_dir):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_summary(user_id='u', period='day:2026-06-05',
                           body='Tagessummary', source_ids=[1, 2],
                           model_used='opencode::deepseek-v4-flash-free')
        VaultRenderer().render_one(n)
        path = vault_dir / 'u' / '_index' / 'by-day' / '2026-06-05.md'
        assert path.exists()
        assert 'Tagessummary' in path.read_text()


def test_cleanup_removes_orphan_file(app, vault_dir):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_note(user_id='u', app='a', title='gone', body='',
                         tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
        path = vault_dir / 'u' / 'a' / 'notes' / 'gone.md'
        assert path.exists()

        from datetime import datetime, timezone
        n.deleted_at = datetime.now(timezone.utc)
        db.session.commit()
        VaultRenderer().cleanup_deleted()
        assert not path.exists()


def test_rebuild_all_idempotent(app, vault_dir):
    with app.app_context():
        w = MemoryWriter()
        w.write_note(user_id='u', app='a', title='one', body='1', tags=[],
                     folder=None, slug=None)
        w.write_note(user_id='u', app='a', title='two', body='2', tags=[],
                     folder=None, slug=None)
        r = VaultRenderer()
        r.rebuild_all()
        r.rebuild_all()
        files = list((vault_dir / 'u' / 'a' / 'notes').iterdir())
        assert sorted(f.name for f in files) == ['one.md', 'two.md']


def test_check_stale_rerenders_modified(app, vault_dir):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_note(user_id='u', app='a', title='hi', body='v1', tags=[],
                         folder=None, slug=None)
        r = VaultRenderer()
        r.render_one(n)
        path = vault_dir / 'u' / 'a' / 'notes' / 'hi.md'
        assert 'v1' in path.read_text()

        from datetime import datetime, timezone, timedelta
        n.body = 'v2'
        n.updated_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        db.session.commit()

        r.check_stale()
        assert 'v2' in path.read_text()
