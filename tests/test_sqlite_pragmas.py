# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite hardening: WAL + busy_timeout must be active on the app engine.

Regression context (2026-09-29): with SQLite defaults, concurrent writers —
gunicorn request handlers, the health/queue worker and the new eval run worker —
made the loser of each write race fail with "database is locked" and silently
drop its row (42 dropped usage-event / memory-audit writes in 10 minutes while
an eval run was writing results).
"""



from config import Config
from database import db, SQLITE_BUSY_TIMEOUT_MS, configure_sqlite_engine


class _FakeApp:
    def __init__(self, uri):
        self.config = {'SQLALCHEMY_DATABASE_URI': uri}


def test_skips_non_sqlite_databases():
    assert configure_sqlite_engine(_FakeApp('postgresql://user@host/db')) is False


def test_wal_and_busy_timeout_are_applied(tmp_path, monkeypatch):
    """A file-backed app DB must report journal_mode=wal + the busy timeout."""
    import app as app_module

    uri = f'sqlite:///{tmp_path}/pragmas.db'
    monkeypatch.setattr(Config, 'DATABASE_URL', uri)
    monkeypatch.setattr(Config, 'SQLALCHEMY_DATABASE_URI', uri)

    application = app_module.create_app()
    with application.app_context():
        with db.engine.connect() as conn:
            journal_mode = conn.exec_driver_sql('PRAGMA journal_mode').scalar()
            busy_timeout = conn.exec_driver_sql('PRAGMA busy_timeout').scalar()
            synchronous = conn.exec_driver_sql('PRAGMA synchronous').scalar()

    assert str(journal_mode).lower() == 'wal'
    assert int(busy_timeout) == SQLITE_BUSY_TIMEOUT_MS
    assert int(synchronous) == 1          # NORMAL
