"""SQLAlchemy Setup."""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


#: Applied to every SQLite connection (see configure_sqlite_engine).
SQLITE_BUSY_TIMEOUT_MS = 30000


def configure_sqlite_engine(app) -> bool:
    """Harden the app's SQLite engine for concurrent writers.

    The service writes from several threads at once (gunicorn request handlers,
    the health/queue worker, the eval run worker). With SQLite's defaults — no
    WAL, `busy_timeout` 5 s — the loser of a write race raises
    "database is locked" and its row is dropped; observed live on 2026-09-29
    while an eval run was writing results: 42 dropped usage-event / memory-audit
    writes in 10 minutes (logged only as warnings).

    `journal_mode=WAL` lets readers run while one writer commits,
    `busy_timeout=30000` makes a competing writer wait instead of failing, and
    `synchronous=NORMAL` is the safe/standard pairing with WAL.

    Returns False (no-op) when the configured database is not SQLite.
    """
    uri = str(app.config.get('SQLALCHEMY_DATABASE_URI', '') or '')
    if not uri.startswith('sqlite'):
        return False

    from sqlalchemy import event

    with app.app_context():
        engine = db.engine          # cached per app, so the listener lands on it

    @event.listens_for(engine, 'connect')
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute('PRAGMA journal_mode=WAL')
            cursor.execute(f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}')
            cursor.execute('PRAGMA synchronous=NORMAL')
        finally:
            cursor.close()

    return True
