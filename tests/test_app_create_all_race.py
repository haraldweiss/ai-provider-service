# SPDX-License-Identifier: AGPL-3.0-or-later
"""db.create_all() must tolerate the concurrent-worker race on a fresh DB.

Two gunicorn workers can both call create_all() on a fresh SQLite file; the
loser of the race sees 'table ... already exists'. The end state (tables
exist) is correct, so that specific error must be swallowed — but any other
OperationalError (disk I/O, permissions, ...) must still propagate.
"""
from __future__ import annotations
import pytest
from sqlalchemy.exc import OperationalError
import app as appmod


class _DB:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def create_all(self):
        self.calls += 1
        if self._exc:
            raise self._exc


def _op_error(message: str) -> OperationalError:
    return OperationalError('CREATE TABLE foo (...)', {}, Exception(message))


def test_safe_create_all_tolerates_already_exists():
    db = _DB(_op_error('table provider_configs already exists'))
    appmod._safe_create_all(db)  # must not raise
    assert db.calls == 1


def test_safe_create_all_reraises_other_operational_errors():
    db = _DB(_op_error('disk I/O error'))
    with pytest.raises(OperationalError):
        appmod._safe_create_all(db)


def test_safe_create_all_passes_through_on_success():
    db = _DB(None)
    appmod._safe_create_all(db)
    assert db.calls == 1
