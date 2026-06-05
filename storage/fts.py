"""SQLite FTS5 full-text search for memory notes.

Creates and manages a virtual FTS5 table over MemoryNote.title + body.
Kept in sync via SQLite triggers. Use raw SQL since SQLAlchemy doesn't
support virtual tables natively.
"""

from __future__ import annotations
import logging
from typing import Optional
from sqlalchemy import text
from database import db

logger = logging.getLogger(__name__)

_FTS_TABLE = 'memory_notes_fts'


def ensure_fts() -> None:
    """Create FTS5 virtual table + triggers if they don't exist."""
    engine = db.engine
    with engine.connect() as conn:
        conn.execute(text(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE}
            USING fts5(
                title, body,
                content='memory_notes',
                content_rowid='id',
                tokenize='porter unicode61'
            )
        """))
        conn.execute(text(f"""
            CREATE TRIGGER IF NOT EXISTS {_FTS_TABLE}_ai AFTER INSERT ON memory_notes
            BEGIN
                INSERT INTO {_FTS_TABLE}(rowid, title, body)
                VALUES (new.id, new.title, new.body);
            END
        """))
        conn.execute(text(f"""
            CREATE TRIGGER IF NOT EXISTS {_FTS_TABLE}_ad AFTER DELETE ON memory_notes
            BEGIN
                INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, title, body)
                VALUES ('delete', old.id, old.title, old.body);
            END
        """))
        conn.execute(text(f"""
            CREATE TRIGGER IF NOT EXISTS {_FTS_TABLE}_au AFTER UPDATE ON memory_notes
            BEGIN
                INSERT INTO {_FTS_TABLE}({_FTS_TABLE}, rowid, title, body)
                VALUES ('delete', old.id, old.title, old.body);
                INSERT INTO {_FTS_TABLE}(rowid, title, body)
                VALUES (new.id, new.title, new.body);
            END
        """))
        conn.commit()


def search(query: str, user_id: Optional[str] = None,
           limit: int = 50, offset: int = 0) -> list[int]:
    """Return matching MemoryNote ids sorted by FTS rank.

    If user_id is provided, restricts to notes owned by that user.
    """
    if not query or not query.strip():
        return []

    sanitized = _sanitize_fts_query(query)
    if not sanitized:
        return []

    where = f"WHERE {_FTS_TABLE} MATCH :q"
    params: dict = {'q': sanitized, 'limit': limit, 'offset': offset}
    if user_id:
        where += ' AND mn.user_id = :uid'
        params['uid'] = user_id

    sql = text(f"""
        SELECT mn.id
        FROM memory_notes mn
        JOIN {_FTS_TABLE} ON mn.id = {_FTS_TABLE}.rowid
        {where}
        ORDER BY rank
        LIMIT :limit OFFSET :offset
    """)
    with db.engine.connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [r[0] for r in rows]


def rebuild_index() -> int:
    """Rebuild FTS index from scratch (e.g., after bulk import)."""
    with db.engine.connect() as conn:
        conn.execute(text(f"INSERT INTO {_FTS_TABLE}({_FTS_TABLE}) VALUES('rebuild')"))
        conn.commit()
    logger.info('FTS index rebuilt')
    return 0


def _sanitize_fts_query(raw: str) -> str:
    """Escape FTS5 special chars and append * for prefix matching."""
    # Remove FTS5 operators that users shouldn't inject
    import re
    cleaned = re.sub(r'[^\w\s\u00c0-\u024f-]', ' ', raw)
    terms = [t.strip() for t in cleaned.split() if t.strip()]
    if not terms:
        return ''
    # Prefix match on each term
    return ' AND '.join(f'"{t}"*' for t in terms)
