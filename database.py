"""SQLAlchemy Setup."""

from contextlib import contextmanager
import os
from urllib.parse import parse_qs, unquote, urlsplit

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

    `busy_timeout=30000` makes a competing writer wait instead of failing, and
    `synchronous=NORMAL` is the safe/standard pairing with WAL. WAL is enabled
    once during serialized startup, rather than racing on every connection.

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
            cursor.execute(f'PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}')
            cursor.execute('PRAGMA synchronous=NORMAL')
        finally:
            cursor.close()

    return True


@contextmanager
def sqlite_startup(engine):
    """Serialize WAL activation and the entire schema bootstrap per DB file.

    Acquire before opening the first SQLite connection: changing journal mode
    can fail immediately under another worker's lock, even with busy_timeout.
    The lock covers create_all's metadata checks as well as DDL, additive
    migrations and FTS, so no worker observes another's partial schema.

    The sidecar stays in place; unlinking it could let waiters lock different
    inodes. flock is released on close, including exceptions/process exit.
    In-memory SQLite and other backends need no filesystem lock.
    """
    if engine.dialect.name != 'sqlite':
        yield
        return

    arguments, options = engine.dialect.create_connect_args(engine.url)
    database = arguments[0]
    if options.get('uri'):
        uri = urlsplit(database)
        if parse_qs(uri.query).get('mode') == ['memory']:
            database = ':memory:'
        else:
            database = unquote(uri.path)

    if not database or database == ':memory:':
        yield
        return

    import fcntl

    lock_path = os.path.realpath(database) + '.startup.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with engine.connect() as connection:
            connection.exec_driver_sql('PRAGMA journal_mode=WAL')
        yield
    finally:
        os.close(descriptor)
