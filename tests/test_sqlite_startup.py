"""Real, isolated processes exercise the Gunicorn SQLite startup boundary."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from sqlalchemy import create_engine

from database import sqlite_startup


ROOT = Path(__file__).resolve().parents[1]
WORKER = r'''
import os
from pathlib import Path
import sys
import time

from sqlalchemy import event
from sqlalchemy.engine import Engine
import worker
worker.start = lambda app: None  # No provider/network polling in this DB test.
from app import create_app
from database import db

directory, role, phase = Path(sys.argv[1]), sys.argv[2], sys.argv[3]

def wait(name):
    deadline = time.monotonic() + 20
    while not (directory / name).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('barrier timed out: ' + name)
        time.sleep(.01)

def signal(name):
    (directory / name).touch()

@event.listens_for(Engine, 'do_connect')
def before_connect(*args):
    if role == 'first' and phase == 'connect':
        signal('first-ready')
        wait('release-first')

@event.listens_for(Engine, 'before_cursor_execute')
def before_sql(conn, cursor, statement, parameters, context, executemany):
    if phase == 'migration-error' and statement.startswith('ALTER TABLE user_profiles'):
        raise RuntimeError('test-only migration denied')
    if phase == 'schema' and statement.lstrip().startswith('CREATE TABLE provider_configs'):
        signal(role + '-ready')
        if role == 'first':
            wait('release-first')
        else:
            wait('first-created')

@event.listens_for(Engine, 'after_cursor_execute')
def after_sql(conn, cursor, statement, parameters, context, executemany):
    if role == 'first' and phase == 'schema' and statement.lstrip().startswith('CREATE TABLE provider_configs'):
        signal('first-created')
        wait('finish-schema')

signal(role + '-starting')
app = create_app()
with app.test_client() as client:
    assert client.get('/health').status_code == 200
    response = client.get('/providers', headers={'Authorization': 'Bearer startup-test-token'})
    assert response.status_code == 200
    assert b'opencode' in response.data
with app.app_context():
    with db.engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'
        assert connection.exec_driver_sql('PRAGMA busy_timeout').scalar() == 30000
        assert connection.exec_driver_sql('PRAGMA synchronous').scalar() == 1
        assert connection.exec_driver_sql('SELECT count(*) FROM memory_notes').scalar() >= 0
signal(role + '-done')
'''


def _wait_for(path, timeout=20):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(.01)
    return True


def _start(directory, role, phase, uri=None):
    # No developer .env, provider keys, production paths, or inherited Config.
    env = {
        'PATH': os.environ.get('PATH', ''),
        'PYTHONPATH': str(ROOT),
        'PYTHON_DOTENV_DISABLED': '1',
        'DATABASE_URL': uri or 'sqlite:///' + str(directory / 'startup.db'),
        'MASTER_KEY': '8hbXucPt-LumWh0Ul9f9wka6VHzAHE29LvU52R3pEDA=',
        'SERVICE_TOKEN': 'startup-test-token',
    }
    return subprocess.Popen(
        [sys.executable, '-c', WORKER, str(directory), role, phase],
        cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize('phase', ['connect', 'schema'])
def test_concurrent_cold_start_waits_for_complete_database(tmp_path, phase):
    """A worker cannot use a partially initialized WAL/schema/FTS database.

    Hold worker one before its first connection or first CREATE. In the
    schema case worker two would plan the same missing tables, collide on
    provider_configs, swallow that error, and create FTS triggers before
    memory_notes exists. Barriers force that historical interleaving.
    """
    processes = []
    try:
        first = _start(tmp_path, 'first', phase)
        processes.append(first)
        assert _wait_for(tmp_path / 'first-ready')
        second = _start(tmp_path, 'second', phase)
        processes.append(second)
        assert _wait_for(tmp_path / 'second-starting')
        # Before the fix this worker enters DDL (schema) or completes boot
        # (connect). With serialization it waits before opening SQLite.
        early = _wait_for(tmp_path / ('second-ready' if phase == 'schema' else 'second-done'), 1)
        (tmp_path / 'release-first').touch()
        if phase == 'schema':
            assert _wait_for(tmp_path / 'first-created')
            if early:
                second.wait(timeout=20)
            (tmp_path / 'finish-schema').touch()
        outcomes = [process.communicate(timeout=30) for process in processes]
        assert [process.returncode for process in processes] == [0, 0], outcomes
        assert not early, 'second worker accessed an unfinished database'
        with sqlite3.connect(tmp_path / 'startup.db') as connection:
            assert connection.execute('PRAGMA integrity_check').fetchone() == ('ok',)
            assert connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_notes_fts_%'"
            ).fetchone() == (3,)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_concurrent_existing_database_preserves_rows_and_migrates_email(tmp_path):
    """Both workers finish the additive migration without replacing data."""
    from cryptography.fernet import Fernet
    cipher = Fernet(b'8hbXucPt-LumWh0Ul9f9wka6VHzAHE29LvU52R3pEDA=')
    ciphertext = cipher.encrypt(b'{"api_key":"test-only-key"}').decode()
    # Bootstrap a real existing database, then model the older email-less schema.
    process = _start(tmp_path, 'seed', 'existing')
    output = process.communicate(timeout=30)
    assert process.returncode == 0, output
    with sqlite3.connect(tmp_path / 'startup.db') as connection:
        connection.execute('ALTER TABLE user_profiles DROP COLUMN email')
        connection.execute("INSERT INTO user_profiles(user_id, alias) VALUES('alice', 'Alice')")
        connection.execute(
            'INSERT INTO provider_configs(user_id, provider_id, config_encrypted, '
            'queue_when_unavailable, queue_ttl_hours) VALUES(?, ?, ?, ?, ?)',
            ('alice', 'openai', ciphertext, 1, 24),
        )
    processes = [_start(tmp_path, role, 'existing') for role in ('first', 'second')]
    try:
        for process in processes:
            output = process.communicate(timeout=30)
            assert process.returncode == 0, output
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
    with sqlite3.connect(tmp_path / 'startup.db') as connection:
        assert connection.execute('SELECT user_id, alias, email FROM user_profiles').fetchall() == [
            ('alice', 'Alice', None),
        ]
        stored = connection.execute('SELECT config_encrypted FROM provider_configs').fetchone()[0]
        assert stored == ciphertext
        assert cipher.decrypt(stored.encode()) == b'{"api_key":"test-only-key"}'


@pytest.mark.parametrize('uri', [
    'sqlite:///:memory:',
    'sqlite:///file::memory:?cache=shared&uri=true',
    'sqlite:///file:startup-memory?mode=memory&cache=shared&uri=true',
])
def test_memory_databases_need_no_lock_file(tmp_path, monkeypatch, uri):
    monkeypatch.chdir(tmp_path)
    engine = create_engine(uri)
    try:
        with sqlite_startup(engine), engine.connect() as connection:
            assert connection.exec_driver_sql('SELECT 1').scalar() == 1
        assert list(tmp_path.iterdir()) == []
    finally:
        engine.dispose()


def test_failed_startup_releases_lock_and_propagates_error(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'startup.db'))
    try:
        with pytest.raises(RuntimeError, match='failed migration'):
            with sqlite_startup(engine):
                raise RuntimeError('failed migration')
        process = _start(tmp_path, 'recovery', 'existing')
        try:
            output = process.communicate(timeout=30)
            assert process.returncode == 0, output
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
    finally:
        engine.dispose()


def test_email_migration_failure_aborts_boot_and_allows_recovery(tmp_path):
    process = _start(tmp_path, 'seed', 'existing')
    output = process.communicate(timeout=30)
    assert process.returncode == 0, output
    with sqlite3.connect(tmp_path / 'startup.db') as connection:
        connection.execute('ALTER TABLE user_profiles DROP COLUMN email')
    process = _start(tmp_path, 'failed', 'migration-error')
    output = process.communicate(timeout=30)
    assert process.returncode != 0, 'worker booted with an incomplete schema'
    assert 'test-only migration denied' in output[1]
    process = _start(tmp_path, 'recovery', 'existing')
    output = process.communicate(timeout=30)
    assert process.returncode == 0, output
