"""FTS5 full-text search tests."""

from storage.fts import ensure_fts, search, rebuild_index


def test_fts_created(app):
    with app.app_context():
        ensure_fts()
        from sqlalchemy import text
        from database import db
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_notes_fts'")
            ).fetchall()
            assert len(rows) >= 1


def test_auto_index_on_insert(app):
    with app.app_context():
        ensure_fts()
        from storage.memory import MemoryWriter
        w = MemoryWriter()
        w.write_note(user_id='u', app='a', title='Hello World',
                      body='This is a test note', tags=[], folder=None, slug=None)
        ids = search('hello', user_id='u')
        assert 1 in ids


def test_search_returns_matching(app):
    with app.app_context():
        ensure_fts()
        from storage.memory import MemoryWriter
        w = MemoryWriter()
        w.write_note(user_id='u', app='a', title='Python tips',
                      body='Use list comprehensions', tags=[], folder=None, slug=None)
        w.write_note(user_id='u', app='a', title='Java tips',
                      body='Use interfaces', tags=[], folder=None, slug=None)
        ids = search('python', user_id='u')
        match = [n for n in ids if n]
        assert len(match) >= 1


def test_search_scoped_to_user(app):
    with app.app_context():
        ensure_fts()
        from storage.memory import MemoryWriter
        w = MemoryWriter()
        w.write_note(user_id='alice', app='a', title='Secret',
                      body='alice private data', tags=[], folder=None, slug=None)
        ids = search('alice', user_id='bob')
        assert len(ids) == 0


def test_empty_query_returns_empty(app):
    with app.app_context():
        assert search('') == []
        assert search('   ') == []
        assert search(None) == []


def test_rebuild_index(app):
    with app.app_context():
        ensure_fts()
        rebuild_index()
