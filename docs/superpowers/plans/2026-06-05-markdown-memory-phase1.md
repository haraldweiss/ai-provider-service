# Markdown Memory Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-user, app-aware markdown memory layer to ai-provider-service: every chat is auto-audited, apps can write structured notes/events, nightly cron produces LLM summaries. SQLite is source of truth; filesystem vault under `VAULT_PATH` is a rendered read-only view.

**Architecture:** New SQLAlchemy table `memory_notes` (polymorphic by `kind`) + `summary_jobs`. Three new isolated components: `MemoryWriter` (DB inserts), `VaultRenderer` (DB → `.md` files), `SummaryJob` (nightly aggregates via cheap-first dispatcher). New blueprints `memory_api` and `vault_api`. Audit is wired through the existing dispatcher's `_execute()`; background work runs via systemd timers calling `flask` CLI commands.

**Tech Stack:** Python 3.9+, Flask, Flask-SQLAlchemy, click (CLI), pytest. No Alembic — the project uses `db.create_all()` on startup.

**Spec:** [docs/superpowers/specs/2026-06-05-markdown-memory-design.md](../specs/2026-06-05-markdown-memory-design.md)

**Branch:** `feat/memory-phase1`

---

## Agent-Routing per AGENTS.md §2

Each task is tagged. Opencode runs the bulk code; Claude Code handles security-critical edits, deploy, and docs.

| Task | Agent | Reason |
|---|---|---|
| 1 — Config keys | opencode | Trivial extension of Config class |
| 2 — Slug utility | opencode | Pure function + unit tests |
| 3 — DB models | opencode | New ORM models, same pattern as existing |
| 4 — MemoryWriter | opencode | New module + unit tests |
| 5 — VaultRenderer | opencode | New module + unit tests against tempdir |
| 6 — Dispatcher hook | **claude-code** | Touches chat hot path |
| 7 — Memory API notes | **claude-code** | Auth scoping is security-critical |
| 8 — Memory API events | opencode | After T7 pattern is established |
| 9 — Memory API audit + summarize | opencode | Same pattern, mostly read-only |
| 10 — Vault export API | **claude-code** | Path-traversal protection is security-critical |
| 11 — SummaryJob + CLI | opencode | New module + click command + tests |
| 12 — Self-heal cron + CLI | opencode | New click command + tests |
| 13 — systemd units | **claude-code** | VPS deploy, SELinux context |
| 14 — Documentation | **claude-code** | AGENTS.md/README/OPERATIONS sync per §5.1 |

When handing off to opencode, point it at this plan file and the spec. It can self-execute tagged `opencode` tasks. Hand the worktree back for `claude-code` tasks.

---

## File Structure

**New files:**
- `storage/memory_models.py` — `MemoryNote`, `SummaryJob` ORM models (T3)
- `storage/slug.py` — slugify + collision helper (T2)
- `storage/memory.py` — `MemoryWriter` (T4)
- `storage/vault_renderer.py` — `VaultRenderer` (T5)
- `agents/__init__.py` — empty package marker (T11)
- `agents/summary_job.py` — `SummaryJob` runner (T11)
- `api/memory_api.py` — Memory API blueprint (T7, T8, T9)
- `api/vault_api.py` — Vault export blueprint (T10)
- `deploy/ai-provider-summary.service` — systemd unit (T13)
- `deploy/ai-provider-summary.timer` — systemd unit (T13)
- `deploy/ai-provider-vault-render.service` — systemd unit (T13)
- `deploy/ai-provider-vault-render.timer` — systemd unit (T13)
- `tests/test_memory_slug.py` (T2)
- `tests/test_memory_models.py` (T3)
- `tests/test_memory_writer.py` (T4)
- `tests/test_vault_renderer.py` (T5)
- `tests/test_dispatcher_audit_hook.py` (T6)
- `tests/test_memory_api_notes.py` (T7)
- `tests/test_memory_api_events.py` (T8)
- `tests/test_memory_api_audit.py` (T9)
- `tests/test_memory_api_summarize.py` (T9)
- `tests/test_vault_api.py` (T10)
- `tests/test_summary_job.py` (T11)
- `tests/test_vault_render_cli.py` (T12)

**Modified files:**
- `config.py` — add `VAULT_PATH`, `MEMORY_ENABLED`, `SUMMARY_PROFILE`, `SUMMARY_MAX_NOTES_PER_DAY`, `MEMORY_FREE_MODELS` (T1)
- `app.py` — import new models, register blueprints, add CLI commands (T3, T7, T10, T11, T12)
- `dispatcher.py` — call MemoryWriter.write_audit in `_execute()` (T6)
- `cli.py` — add `summary-job` and `vault-render` click commands (T11, T12)
- `AGENTS.md` — new hard rule + production reference (T14)
- `README.md` — setup section update (T14)
- `OPERATIONS.md` — backup posture, "vault is cache" (T14)
- `requirements.txt` — no changes expected (re-verify in T1)

---

## Task 1 — Config keys

**Tag:** opencode
**Files:**
- Modify: `config.py` (after line 45, before `validate()`)
- Test: `tests/test_memory_config.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_config.py`:

```python
"""Config keys for markdown memory feature."""

import os
import importlib


def _reload_config():
    import config
    importlib.reload(config)
    return config.Config


def test_defaults():
    for var in ('VAULT_PATH', 'MEMORY_ENABLED', 'SUMMARY_PROFILE',
                'SUMMARY_MAX_NOTES_PER_DAY', 'MEMORY_FREE_MODELS'):
        os.environ.pop(var, None)
    Config = _reload_config()
    assert Config.VAULT_PATH.endswith('vault')
    assert Config.MEMORY_ENABLED is False
    assert Config.SUMMARY_PROFILE == 'cheap-first'
    assert Config.SUMMARY_MAX_NOTES_PER_DAY == 200
    assert Config.MEMORY_FREE_MODELS == []


def test_env_override():
    os.environ['VAULT_PATH'] = '/tmp/test-vault'
    os.environ['MEMORY_ENABLED'] = 'true'
    os.environ['SUMMARY_PROFILE'] = 'cheap-first'
    os.environ['SUMMARY_MAX_NOTES_PER_DAY'] = '500'
    os.environ['MEMORY_FREE_MODELS'] = 'opencode::deepseek-v4-flash-free,ollama::mistral'
    Config = _reload_config()
    try:
        assert Config.VAULT_PATH == '/tmp/test-vault'
        assert Config.MEMORY_ENABLED is True
        assert Config.SUMMARY_MAX_NOTES_PER_DAY == 500
        assert Config.MEMORY_FREE_MODELS == ['opencode::deepseek-v4-flash-free', 'ollama::mistral']
    finally:
        for var in ('VAULT_PATH', 'MEMORY_ENABLED', 'SUMMARY_PROFILE',
                    'SUMMARY_MAX_NOTES_PER_DAY', 'MEMORY_FREE_MODELS'):
            os.environ.pop(var, None)
        _reload_config()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_config.py -v`
Expected: FAIL — `AttributeError: type object 'Config' has no attribute 'VAULT_PATH'`

- [ ] **Step 3: Add the keys to `config.py`**

Insert after the line `SECRET_KEY = os.getenv('SECRET_KEY', '')` (around line 45), before the `@classmethod validate`:

```python
    # Markdown memory (Phase 1)
    VAULT_PATH = os.getenv('VAULT_PATH', os.path.join(os.path.dirname(__file__), 'vault'))
    MEMORY_ENABLED = os.getenv('MEMORY_ENABLED', 'false').lower() == 'true'
    SUMMARY_PROFILE = os.getenv('SUMMARY_PROFILE', 'cheap-first')
    SUMMARY_MAX_NOTES_PER_DAY = int(os.getenv('SUMMARY_MAX_NOTES_PER_DAY', '200'))
    MEMORY_FREE_MODELS = [
        m.strip() for m in os.getenv('MEMORY_FREE_MODELS', '').split(',') if m.strip()
    ]
```

`MEMORY_FREE_MODELS` is the ordered list of `<provider>::<model>` identifiers tried by the `cheap-first` summary profile. Empty list means summarization is disabled (job records `failed: no free models configured`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_memory_config.py
git commit -m "$(cat <<'EOF'
Add: memory config keys (VAULT_PATH, MEMORY_ENABLED, SUMMARY_*)

Verified: pytest tests/test_memory_config.py ✓ (2/2)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2 — Slug utility

**Tag:** opencode
**Files:**
- Create: `storage/slug.py`
- Test: `tests/test_memory_slug.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_slug.py`:

```python
"""Slug helpers for markdown memory filenames."""

import pytest
from storage.slug import slugify, next_free_slug


def test_basic():
    assert slugify('Hello World') == 'hello-world'


def test_umlauts():
    assert slugify('Über Föhn — heute') == 'uber-fohn-heute'


def test_specials_collapse():
    assert slugify('!!!Hi!!! ???there??? @#$') == 'hi-there'


def test_empty_falls_back_to_note():
    assert slugify('') == 'note'
    assert slugify('   ') == 'note'
    assert slugify('!!!') == 'note'


def test_max_length():
    long = 'a' * 200
    s = slugify(long)
    assert len(s) <= 80
    assert s == 'a' * 80


def test_explicit_slug_validated():
    from storage.slug import validate_explicit_slug
    assert validate_explicit_slug('hello-world') is True
    assert validate_explicit_slug('Hello') is False  # uppercase
    assert validate_explicit_slug('a' * 81) is False  # too long
    assert validate_explicit_slug('') is False
    assert validate_explicit_slug('with space') is False


def test_next_free_slug_no_collision():
    taken = set()
    assert next_free_slug('hello', taken) == 'hello'


def test_next_free_slug_with_collision():
    taken = {'hello', 'hello-2', 'hello-3'}
    assert next_free_slug('hello', taken) == 'hello-4'


def test_next_free_slug_max_attempts_raises():
    taken = {'hello'} | {f'hello-{i}' for i in range(2, 102)}
    with pytest.raises(ValueError, match='slug collision'):
        next_free_slug('hello', taken)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_slug.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storage.slug'`

- [ ] **Step 3: Implement `storage/slug.py`**

```python
"""Slug helpers for markdown memory filenames."""

from __future__ import annotations
import re
import unicodedata

_SLUG_MAX_LEN = 80
_ALLOWED_RE = re.compile(r'^[a-z0-9-]{1,80}$')


def slugify(title: str) -> str:
    """Convert free-form title to filesystem-safe slug.

    Falls back to 'note' for empty / pure-special-char input.
    Truncates to 80 chars.
    """
    if not title:
        return 'note'
    normalized = unicodedata.normalize('NFKD', title)
    ascii_only = ''.join(c for c in normalized if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    s = re.sub(r'[^a-z0-9]+', '-', lowered).strip('-')
    if not s:
        return 'note'
    return s[:_SLUG_MAX_LEN]


def validate_explicit_slug(slug: str) -> bool:
    """True if `slug` matches the strict pattern an app may submit."""
    return bool(_ALLOWED_RE.match(slug or ''))


def next_free_slug(base: str, taken: set[str]) -> str:
    """Return `base`, or `base-2`/`base-3`/… if taken. Max 100 attempts."""
    if base not in taken:
        return base
    for i in range(2, 102):
        candidate = f'{base}-{i}'
        if candidate not in taken:
            return candidate
    raise ValueError(f'slug collision: too many variants of "{base}"')
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_memory_slug.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add storage/slug.py tests/test_memory_slug.py
git commit -m "$(cat <<'EOF'
Add: slug helper for markdown memory filenames

Verified: pytest tests/test_memory_slug.py ✓ (8/8)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3 — DB models

**Tag:** opencode
**Files:**
- Create: `storage/memory_models.py`
- Modify: `app.py:41` (extend the model import line)
- Test: `tests/test_memory_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_models.py`:

```python
"""ORM models for memory notes and summary jobs."""

import pytest
from database import db
from storage.memory_models import MemoryNote, SummaryJob, MemoryKind


def test_create_note(app):
    with app.app_context():
        n = MemoryNote(
            user_id='harald', app='bewerbungstracker', kind=MemoryKind.NOTE,
            folder='notes', slug='meeting-acme',
            title='Meeting with Acme', body='Discussed terms.',
            tags=['meetings'], extra={},
        )
        db.session.add(n)
        db.session.commit()
        assert n.id is not None
        assert n.created_at is not None


def test_unique_constraint(app):
    with app.app_context():
        n1 = MemoryNote(user_id='harald', app='bt', kind=MemoryKind.NOTE,
                        folder='notes', slug='dup', title='A', body='', tags=[], extra={})
        db.session.add(n1)
        db.session.commit()
        n2 = MemoryNote(user_id='harald', app='bt', kind=MemoryKind.NOTE,
                        folder='notes', slug='dup', title='B', body='', tags=[], extra={})
        db.session.add(n2)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()


def test_chat_request_id_unique_partial(app):
    """audit notes with same chat_request_id must collide; null chat_request_id
    must NOT collide."""
    with app.app_context():
        a1 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.AUDIT,
                        folder='audit', slug='a', title='', body='',
                        tags=[], extra={'chat_request_id': 'req-1'},
                        chat_request_id='req-1')
        a2 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.AUDIT,
                        folder='audit', slug='b', title='', body='',
                        tags=[], extra={'chat_request_id': 'req-1'},
                        chat_request_id='req-1')
        db.session.add(a1)
        db.session.commit()
        db.session.add(a2)
        with pytest.raises(Exception):
            db.session.commit()
        db.session.rollback()

        b1 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.NOTE,
                        folder='notes', slug='x', title='', body='',
                        tags=[], extra={})
        b2 = MemoryNote(user_id='harald', app='gw', kind=MemoryKind.NOTE,
                        folder='notes', slug='y', title='', body='',
                        tags=[], extra={})
        db.session.add(b1)
        db.session.add(b2)
        db.session.commit()


def test_soft_delete_default_null(app):
    with app.app_context():
        n = MemoryNote(user_id='u', app='a', kind=MemoryKind.NOTE,
                       folder='notes', slug='s', title='t', body='', tags=[], extra={})
        db.session.add(n)
        db.session.commit()
        assert n.deleted_at is None


def test_summary_job_create(app):
    with app.app_context():
        j = SummaryJob(period='day:2026-06-05', user_id='harald', status='pending')
        db.session.add(j)
        db.session.commit()
        assert j.id is not None
        assert j.status == 'pending'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storage.memory_models'`

- [ ] **Step 3: Implement `storage/memory_models.py`**

```python
"""ORM models for markdown memory: MemoryNote (polymorphic) + SummaryJob."""

from __future__ import annotations
import enum
from datetime import datetime, timezone
from database import db


class MemoryKind(str, enum.Enum):
    AUDIT = 'audit'
    NOTE = 'note'
    EVENT = 'event'
    SUMMARY = 'summary'


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryNote(db.Model):
    """Polymorphic by `kind`. One table covers audit notes, app-written notes,
    typed events, and LLM-generated summaries. See
    docs/superpowers/specs/2026-06-05-markdown-memory-design.md for layout."""
    __tablename__ = 'memory_notes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), nullable=False)
    app = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.Enum(MemoryKind), nullable=False)
    folder = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(120), nullable=False)
    title = db.Column(db.String(255), nullable=False, default='')
    body = db.Column(db.Text, nullable=False, default='')
    tags = db.Column(db.JSON, nullable=False, default=list)
    extra = db.Column(db.JSON, nullable=False, default=dict)
    chat_request_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'folder', 'slug', name='uq_memory_user_folder_slug'),
        db.Index(
            'uq_memory_chat_request_id', 'chat_request_id',
            unique=True, sqlite_where=db.text('chat_request_id IS NOT NULL'),
        ),
        db.Index('ix_memory_user_kind', 'user_id', 'kind'),
        db.Index('ix_memory_user_folder', 'user_id', 'folder'),
        db.Index('ix_memory_user_created', 'user_id', 'created_at'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'app': self.app,
            'kind': self.kind.value,
            'folder': self.folder,
            'slug': self.slug,
            'title': self.title,
            'body': self.body,
            'tags': self.tags or [],
            'extra': self.extra or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'deleted_at': self.deleted_at.isoformat() if self.deleted_at else None,
        }


class SummaryJob(db.Model):
    """Tracks one execution of the nightly summary job per (period, user)."""
    __tablename__ = 'summary_jobs'

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='pending')
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    error_msg = db.Column(db.Text, nullable=True)
    model_used = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        db.Index('ix_summary_user_period', 'user_id', 'period'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'period': self.period,
            'user_id': self.user_id,
            'status': self.status,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'error_msg': self.error_msg,
            'model_used': self.model_used,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
```

- [ ] **Step 4: Wire the models into app.py**

Edit `app.py` line 41 — extend the existing model import so `db.create_all()` picks up the new tables:

```python
        from storage.models import ProviderConfig, RequestQueue, UsageEvent, ProviderGrant, UserProfile  # noqa: F401
        from storage.memory_models import MemoryNote, SummaryJob  # noqa: F401
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_memory_models.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Confirm full suite still green**

Run: `pytest -q`
Expected: existing 142 + 13 new (config + slug + models) = 155 passing.

- [ ] **Step 7: Commit**

```bash
git add storage/memory_models.py app.py tests/test_memory_models.py
git commit -m "$(cat <<'EOF'
Add: MemoryNote and SummaryJob ORM models

Polymorphic single-table design keyed by `kind` (audit/note/event/summary).
Partial-unique index on chat_request_id for audit idempotency.

Verified: pytest tests/test_memory_models.py ✓ (5/5), full suite ✓

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4 — MemoryWriter

**Tag:** opencode
**Files:**
- Create: `storage/memory.py`
- Test: `tests/test_memory_writer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_writer.py`:

```python
"""MemoryWriter — DB-only insert path. Filesystem render is tested separately."""

import pytest
from database import db
from storage.memory import MemoryWriter, NoteAlreadyExists
from storage.memory_models import MemoryNote, MemoryKind


def test_write_note_minimal(app):
    with app.app_context():
        w = MemoryWriter()
        note = w.write_note(user_id='harald', app='bt',
                            title='Hello', body='World', tags=[], folder=None, slug=None)
        assert note.id is not None
        assert note.kind == MemoryKind.NOTE
        assert note.folder == 'bt/notes'
        assert note.slug == 'hello'


def test_write_note_slug_collision_autosuffix(app):
    with app.app_context():
        w = MemoryWriter()
        w.write_note(user_id='u', app='a', title='Same Title', body='', tags=[], folder=None, slug=None)
        n2 = w.write_note(user_id='u', app='a', title='Same Title', body='', tags=[], folder=None, slug=None)
        assert n2.slug == 'same-title-2'


def test_write_note_shared_folder(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_note(user_id='u', app='a', title='Cross', body='', tags=[],
                         folder='_shared', slug=None)
        assert n.folder == '_shared/notes'


def test_write_note_explicit_slug(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_note(user_id='u', app='a', title='X', body='', tags=[],
                         folder=None, slug='custom-slug')
        assert n.slug == 'custom-slug'


def test_write_note_invalid_explicit_slug_raises(app):
    with app.app_context():
        w = MemoryWriter()
        with pytest.raises(ValueError, match='invalid slug'):
            w.write_note(user_id='u', app='a', title='X', body='', tags=[],
                         folder=None, slug='Bad Slug!')


def test_write_audit_basic(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_audit(
            user_id='harald', app='bt', provider='claude',
            chat_request_id='req-abc12',
            prompt='hi', response='hello',
            tokens={'prompt': 5, 'completion': 7},
            cost_eur=0.0001, latency_ms=120,
            timestamp=None,
        )
        assert n.kind == MemoryKind.AUDIT
        assert n.folder.startswith('bt/audit/')
        assert n.chat_request_id == 'req-abc12'
        assert n.extra['provider'] == 'claude'
        assert '## Prompt' in n.body
        assert '## Response' in n.body


def test_write_audit_idempotent_on_duplicate_request_id(app):
    with app.app_context():
        w = MemoryWriter()
        w.write_audit(user_id='u', app='a', provider='p', chat_request_id='req-x',
                      prompt='', response='', tokens={}, cost_eur=0, latency_ms=0,
                      timestamp=None)
        with pytest.raises(NoteAlreadyExists):
            w.write_audit(user_id='u', app='a', provider='p', chat_request_id='req-x',
                          prompt='different', response='', tokens={}, cost_eur=0,
                          latency_ms=0, timestamp=None)


def test_write_event(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_event(user_id='u', app='bt',
                          event_type='application_created',
                          payload={'company': 'ACME', 'position': 'Engineer'},
                          tags=['jobs'], slug=None)
        assert n.kind == MemoryKind.EVENT
        assert n.folder == 'bt/events/application_created'
        assert n.extra['event_type'] == 'application_created'
        assert n.extra['payload']['company'] == 'ACME'


def test_write_summary(app):
    with app.app_context():
        w = MemoryWriter()
        n = w.write_summary(user_id='u', period='day:2026-06-05',
                            body='Summary text', source_ids=[1, 2, 3],
                            model_used='opencode::deepseek-v4-flash-free')
        assert n.kind == MemoryKind.SUMMARY
        assert n.folder == '_index/by-day'
        assert n.slug == '2026-06-05'
        assert n.extra['source_ids'] == [1, 2, 3]
        assert n.extra['model'] == 'opencode::deepseek-v4-flash-free'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storage.memory'`

- [ ] **Step 3: Implement `storage/memory.py`**

```python
"""MemoryWriter — single entry point for inserting memory_notes rows.

Each public method commits a single row. Filesystem rendering is a separate
concern (see storage.vault_renderer).
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from database import db
from storage.memory_models import MemoryNote, MemoryKind
from storage.slug import slugify, validate_explicit_slug, next_free_slug


class NoteAlreadyExists(Exception):
    """Raised when the unique constraint on (user, folder, slug) or on
    chat_request_id rejects the insert. Carries the existing note id."""

    def __init__(self, existing_id: int, msg: str = ''):
        super().__init__(msg or f'note already exists (id={existing_id})')
        self.existing_id = existing_id


class MemoryWriter:
    """All writes funnel through here. Stateless — instantiate per request or
    keep one instance on the app, both work."""

    def write_note(self, *, user_id: str, app: str, title: str, body: str,
                   tags: list, folder: Optional[str], slug: Optional[str]) -> MemoryNote:
        target_folder = self._resolve_note_folder(app, folder)
        chosen_slug = self._choose_slug(user_id, target_folder, title, slug)
        n = MemoryNote(
            user_id=user_id, app=app, kind=MemoryKind.NOTE,
            folder=target_folder, slug=chosen_slug,
            title=title, body=body, tags=tags or [], extra={},
        )
        db.session.add(n)
        db.session.commit()
        return n

    def write_audit(self, *, user_id: str, app: str, provider: str,
                    chat_request_id: str, prompt: str, response: str,
                    tokens: dict, cost_eur: float, latency_ms: int,
                    timestamp: Optional[datetime]) -> MemoryNote:
        existing = (MemoryNote.query
                    .filter_by(chat_request_id=chat_request_id)
                    .first())
        if existing is not None:
            raise NoteAlreadyExists(existing.id, f'audit for {chat_request_id} exists')

        ts = timestamp or datetime.now(timezone.utc)
        folder = f'{app}/audit/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}'
        slug_base = ts.strftime('%Y%m%dT%H%M%SZ') + '-' + chat_request_id[:12]
        chosen_slug = self._next_free(user_id, folder, slug_base)

        body = f'## Prompt\n\n{prompt}\n\n## Response\n\n{response}\n'
        n = MemoryNote(
            user_id=user_id, app=app, kind=MemoryKind.AUDIT,
            folder=folder, slug=chosen_slug,
            title='', body=body, tags=['chat', 'audit'],
            extra={
                'provider': provider,
                'chat_request_id': chat_request_id,
                'tokens': tokens,
                'cost_eur': cost_eur,
                'latency_ms': latency_ms,
            },
            chat_request_id=chat_request_id,
        )
        db.session.add(n)
        db.session.commit()
        return n

    def write_event(self, *, user_id: str, app: str, event_type: str,
                    payload: dict, tags: list, slug: Optional[str]) -> MemoryNote:
        folder = f'{app}/events/{event_type}'
        chosen_slug = self._choose_slug(user_id, folder, event_type, slug)
        body = f'```json\n{_json_dumps(payload)}\n```\n'
        n = MemoryNote(
            user_id=user_id, app=app, kind=MemoryKind.EVENT,
            folder=folder, slug=chosen_slug,
            title=event_type, body=body, tags=tags or [],
            extra={'event_type': event_type, 'payload': payload},
        )
        db.session.add(n)
        db.session.commit()
        return n

    def write_summary(self, *, user_id: str, period: str, body: str,
                      source_ids: list, model_used: str) -> MemoryNote:
        kind_label, _, value = period.partition(':')
        if kind_label == 'day':
            folder = '_index/by-day'
            slug = value
        elif kind_label == 'app':
            folder = '_index/by-app'
            slug = value
        else:
            raise ValueError(f'unknown period: {period}')

        existing = (MemoryNote.query
                    .filter_by(user_id=user_id, folder=folder, slug=slug,
                               kind=MemoryKind.SUMMARY)
                    .first())
        if existing is not None:
            existing.body = body
            existing.extra = {'source_ids': source_ids, 'model': model_used,
                              'period': period}
            db.session.commit()
            return existing

        n = MemoryNote(
            user_id=user_id, app='gateway', kind=MemoryKind.SUMMARY,
            folder=folder, slug=slug,
            title=period, body=body, tags=['summary'],
            extra={'source_ids': source_ids, 'model': model_used, 'period': period},
        )
        db.session.add(n)
        db.session.commit()
        return n

    def _resolve_note_folder(self, app: str, folder: Optional[str]) -> str:
        if folder is None or folder == '':
            return f'{app}/notes'
        if folder == '_shared':
            return '_shared/notes'
        if not folder.startswith(f'{app}/'):
            raise ValueError(
                f'folder must start with "{app}/" or be "_shared" (got {folder!r})'
            )
        return folder

    def _choose_slug(self, user_id: str, folder: str, title: str,
                     explicit: Optional[str]) -> str:
        if explicit is not None:
            if not validate_explicit_slug(explicit):
                raise ValueError(f'invalid slug: {explicit!r}')
            base = explicit
        else:
            base = slugify(title)
        return self._next_free(user_id, folder, base)

    def _next_free(self, user_id: str, folder: str, base: str) -> str:
        existing = {
            row.slug for row in
            db.session.query(MemoryNote.slug).filter(
                MemoryNote.user_id == user_id,
                MemoryNote.folder == folder,
            ).all()
        }
        return next_free_slug(base, existing)


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_writer.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Confirm suite green**

Run: `pytest -q`
Expected: previous total + 9 new memory-writer = 164 passing.

- [ ] **Step 6: Commit**

```bash
git add storage/memory.py tests/test_memory_writer.py
git commit -m "$(cat <<'EOF'
Add: MemoryWriter (write_note / write_audit / write_event / write_summary)

Single insert point with slug collision handling, audit idempotency via
chat_request_id, and upsert semantics for summary aggregates.

Verified: pytest tests/test_memory_writer.py ✓ (9/9), full suite ✓

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5 — VaultRenderer

**Tag:** opencode
**Files:**
- Create: `storage/vault_renderer.py`
- Test: `tests/test_vault_renderer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vault_renderer.py`:

```python
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
        assert 'kind: note' in content
        assert 'user: harald' in content
        assert 'app: bt' in content
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vault_renderer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'storage.vault_renderer'`

- [ ] **Step 3: Implement `storage/vault_renderer.py`**

```python
"""VaultRenderer — projects DB rows to .md files under VAULT_PATH.

Source of truth is the DB. The vault directory is a rendered cache.
"""

from __future__ import annotations
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
import json
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind

logger = logging.getLogger(__name__)


class VaultRenderer:
    """Stateless. All paths are derived from Config.VAULT_PATH per call so
    tests that monkeypatch the config see updates immediately."""

    def render_one(self, note: MemoryNote) -> Path:
        if note.deleted_at is not None:
            self._remove_one(note)
            return Path()
        path = self._path_for(note)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._compose_markdown(note), encoding='utf-8')
        return path

    def cleanup_deleted(self) -> int:
        removed = 0
        deleted = (MemoryNote.query
                   .filter(MemoryNote.deleted_at.isnot(None))
                   .all())
        for n in deleted:
            if self._remove_one(n):
                removed += 1
        return removed

    def rebuild_all(self, user_id: Optional[str] = None) -> int:
        q = MemoryNote.query.filter(MemoryNote.deleted_at.is_(None))
        if user_id:
            q = q.filter_by(user_id=user_id)
        count = 0
        for n in q.all():
            self.render_one(n)
            count += 1
        return count

    def check_stale(self) -> int:
        count = 0
        for n in MemoryNote.query.filter(MemoryNote.deleted_at.is_(None)).all():
            path = self._path_for(n)
            if not path.exists():
                self.render_one(n)
                count += 1
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                self.render_one(n)
                count += 1
                continue
            db_updated = n.updated_at
            if db_updated.tzinfo is None:
                db_updated = db_updated.replace(tzinfo=timezone.utc)
            if db_updated > mtime:
                self.render_one(n)
                count += 1
        return count

    def _path_for(self, note: MemoryNote) -> Path:
        root = Path(Config.VAULT_PATH)
        return root / note.user_id / note.folder / f'{note.slug}.md'

    def _remove_one(self, note: MemoryNote) -> bool:
        path = self._path_for(note)
        if path.exists():
            try:
                path.unlink()
                return True
            except OSError as e:
                logger.warning(f'vault unlink failed for {path}: {e}')
        return False

    def _compose_markdown(self, note: MemoryNote) -> str:
        frontmatter = {
            'id': note.id,
            'kind': note.kind.value,
            'user': note.user_id,
            'app': note.app,
            'created': (note.created_at.replace(tzinfo=timezone.utc)
                        if note.created_at.tzinfo is None
                        else note.created_at).isoformat(),
            'tags': note.tags or [],
        }
        if note.extra:
            for k, v in note.extra.items():
                frontmatter[k] = v
        if note.chat_request_id:
            frontmatter['chat_request_id'] = note.chat_request_id

        fm_lines = ['---']
        for k, v in frontmatter.items():
            fm_lines.append(f'{k}: {json.dumps(v, ensure_ascii=False)}')
        fm_lines.append('---')
        fm = '\n'.join(fm_lines)

        title_line = f'\n# {note.title}\n' if note.title else ''
        return f'{fm}\n{title_line}\n{note.body}\n'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_vault_renderer.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Confirm suite green**

Run: `pytest -q`
Expected: prior total + 6 new vault-renderer = 170 passing.

- [ ] **Step 6: Commit**

```bash
git add storage/vault_renderer.py tests/test_vault_renderer.py
git commit -m "$(cat <<'EOF'
Add: VaultRenderer (DB rows → markdown files under VAULT_PATH)

Supports render_one, cleanup_deleted, rebuild_all, check_stale. Filesystem
view is a cache; DB is authoritative. Stale-check enables self-heal cron.

Verified: pytest tests/test_vault_renderer.py ✓ (6/6), full suite ✓

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6 — Dispatcher audit hook

**Tag:** **claude-code** (touches chat hot path)
**Files:**
- Modify: `dispatcher.py` (extend `_execute`)
- Test: `tests/test_dispatcher_audit_hook.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatcher_audit_hook.py`:

```python
"""Audit hook in dispatcher._execute writes an audit note via MemoryWriter."""

import os
import pytest
from unittest.mock import patch, MagicMock
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind


@pytest.fixture
def memory_enabled(monkeypatch):
    monkeypatch.setattr(Config, 'MEMORY_ENABLED', True)


def _build_messages():
    return [{'role': 'user', 'content': 'hi'}]


def test_successful_chat_writes_audit(app, memory_enabled):
    with app.app_context():
        fake_response = {
            'content': 'hello',
            'usage': {'input_tokens': 4, 'output_tokens': 2},
        }
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = fake_response
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    from dispatcher import _execute
                    _execute(user_id='harald', provider_id='claude',
                             model='claude-haiku', messages=_build_messages(),
                             max_tokens=100, origin_app='bewerbungstracker')
        audits = MemoryNote.query.filter_by(kind=MemoryKind.AUDIT).all()
        assert len(audits) == 1
        a = audits[0]
        assert a.user_id == 'harald'
        assert a.app == 'bewerbungstracker'
        assert a.extra['provider'] == 'claude'
        assert '## Prompt' in a.body
        assert '## Response' in a.body


def test_failed_chat_does_not_write_audit(app, memory_enabled):
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.side_effect = RuntimeError('boom')
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    from dispatcher import _execute
                    with pytest.raises(RuntimeError):
                        _execute(user_id='u', provider_id='claude', model='m',
                                 messages=_build_messages(), max_tokens=10,
                                 origin_app='bt')
        assert MemoryNote.query.filter_by(kind=MemoryKind.AUDIT).count() == 0


def test_memory_disabled_skips_audit(app):
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = {'content': 'ok', 'usage': {}}
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    from dispatcher import _execute
                    _execute(user_id='u', provider_id='claude', model='m',
                             messages=_build_messages(), max_tokens=10,
                             origin_app='bt')
        assert MemoryNote.query.filter_by(kind=MemoryKind.AUDIT).count() == 0


def test_audit_failure_does_not_break_chat(app, memory_enabled):
    """If MemoryWriter raises, _execute must still return the model response."""
    with app.app_context():
        with patch('dispatcher.get_client') as gc:
            client = MagicMock()
            client.create_message.return_value = {'content': 'ok', 'usage': {}}
            gc.return_value = client
            with patch('dispatcher._load_config', return_value={}):
                with patch('dispatcher.health_tracker.set_status'):
                    with patch('dispatcher._write_audit_note',
                               side_effect=RuntimeError('db full')):
                        from dispatcher import _execute
                        result = _execute(user_id='u', provider_id='claude',
                                          model='m', messages=_build_messages(),
                                          max_tokens=10, origin_app='bt')
                        assert result['content'] == 'ok'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dispatcher_audit_hook.py -v`
Expected: FAIL — `AttributeError: module 'dispatcher' has no attribute '_write_audit_note'`

- [ ] **Step 3: Modify `dispatcher.py`**

Add this helper above `_execute` (right after `_log_usage_event`, around line 80):

```python
def _write_audit_note(
    user_id: str, provider_id: str, origin_app: Optional[str],
    chat_request_id: str, prompt_text: str, response_text: str,
    usage: dict, cost_eur: float, latency_ms: int,
) -> None:
    """Write an audit note via MemoryWriter. Failures are swallowed — audit
    must never break a chat (see spec Flow 1)."""
    from config import Config as _Config
    if not _Config.MEMORY_ENABLED:
        return
    try:
        from storage.memory import MemoryWriter, NoteAlreadyExists
        try:
            MemoryWriter().write_audit(
                user_id=user_id,
                app=origin_app or 'gateway',
                provider=provider_id,
                chat_request_id=chat_request_id,
                prompt=prompt_text,
                response=response_text,
                tokens=usage or {},
                cost_eur=cost_eur,
                latency_ms=latency_ms,
                timestamp=None,
            )
        except NoteAlreadyExists:
            return
    except Exception as e:
        logger.warning(f'memory audit write failed: {e}')
        try:
            db.session.rollback()
        except Exception:
            pass
```

Replace the body of `_execute` from `try:` to the `return result` line with the version below (this preserves the existing usage-logging path and adds the audit hook on success):

```python
def _execute(
    user_id: str, provider_id: str, model: str, messages: list, max_tokens: int,
    config_override: Optional[dict] = None,
    origin_app: Optional[str] = None,
) -> dict:
    cfg = config_override if config_override is not None else _load_config(user_id, provider_id)
    if cfg is None:
        raise ValueError(f"Provider {provider_id} ist nicht konfiguriert für user_id={user_id}")

    client = get_client(provider_id, cfg)
    chat_request_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    try:
        result = client.create_message(model, messages, max_tokens)
        latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        health_tracker.set_status(provider_id, True)
        usage = (result or {}).get('usage') or {}
        _log_usage_event(
            user_id, provider_id, model,
            usage.get('input_tokens'), usage.get('output_tokens'),
            'success', origin_app=origin_app,
        )
        prompt_text = _join_messages(messages)
        response_text = _extract_response_text(result)
        _write_audit_note(
            user_id=user_id, provider_id=provider_id, origin_app=origin_app,
            chat_request_id=chat_request_id,
            prompt_text=prompt_text, response_text=response_text,
            usage=usage, cost_eur=0.0, latency_ms=latency_ms,
        )
        return result
    except Exception as e:
        health_tracker.set_status(provider_id, False, reason=f"{type(e).__name__}: {e}")
        _log_usage_event(
            user_id, provider_id, model, None, None,
            'error', error_message=f"{type(e).__name__}: {e}",
            origin_app=origin_app,
        )
        raise
```

Add these two helpers next to `_write_audit_note`:

```python
def _join_messages(messages: list) -> str:
    """Concatenate user/assistant/system messages into a single prompt string
    for audit storage."""
    lines = []
    for m in messages or []:
        role = m.get('role', '?')
        content = m.get('content', '')
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get('text', '') or '')
                else:
                    parts.append(str(block))
            content = '\n'.join(parts)
        lines.append(f'**{role}**\n{content}')
    return '\n\n'.join(lines)


def _extract_response_text(result: dict) -> str:
    """Pull the assistant response text out of a provider result dict."""
    if not isinstance(result, dict):
        return str(result)
    if isinstance(result.get('content'), str):
        return result['content']
    if isinstance(result.get('content'), list):
        return '\n'.join(
            b.get('text', '') for b in result['content'] if isinstance(b, dict)
        )
    return result.get('text') or result.get('message') or ''
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_dispatcher_audit_hook.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Confirm full suite green (dispatcher tests must still pass)**

Run: `pytest -q`
Expected: 170 prior + 4 hook = 174 passing. Existing dispatcher tests should be untouched.

- [ ] **Step 6: Commit**

```bash
git add dispatcher.py tests/test_dispatcher_audit_hook.py
git commit -m "$(cat <<'EOF'
Add: audit hook in dispatcher._execute (gated by MEMORY_ENABLED)

After each successful provider call, write a memory_notes row with
kind=audit. Failures swallowed — audit must never break a chat. Idempotent
via unique chat_request_id.

Verified: pytest tests/test_dispatcher_audit_hook.py ✓ (4/4), full suite ✓

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7 — Memory API: notes endpoints

**Tag:** **claude-code** (auth scoping is security-critical)
**Files:**
- Create: `api/memory_api.py`
- Modify: `app.py` — register blueprint
- Test: `tests/test_memory_api_notes.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_api_notes.py`:

```python
"""Memory API — notes CRUD with auth scoping."""

import json
import pytest
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind


@pytest.fixture
def user_headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_create_note_returns_201(client, user_headers):
    r = client.post('/memory/notes',
                    headers=user_headers,
                    json={'user_id': 'harald', 'app': 'bt',
                          'title': 'Hello', 'body': 'World', 'tags': ['x']})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert 'id' in body
    assert body['path'].endswith('hello.md')


def test_list_notes_scoped_to_user(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        MemoryWriter().write_note(user_id='harald', app='bt', title='A',
                                  body='', tags=[], folder=None, slug=None)
        MemoryWriter().write_note(user_id='alice', app='bt', title='B',
                                  body='', tags=[], folder=None, slug=None)
    r = client.get('/memory/notes?user_id=harald', headers=user_headers)
    assert r.status_code == 200
    notes = r.get_json()['notes']
    titles = sorted(n['title'] for n in notes)
    assert titles == ['A']


def test_get_single_note(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='X',
                                      body='hi', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.get(f'/memory/notes/{nid}?user_id=harald', headers=user_headers)
    assert r.status_code == 200
    assert r.get_json()['title'] == 'X'


def test_get_other_users_note_returns_404(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='alice', app='bt', title='Y',
                                      body='', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.get(f'/memory/notes/{nid}?user_id=harald', headers=user_headers)
    assert r.status_code == 404


def test_patch_only_editable_kinds(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='X',
                                      body='old', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.patch(f'/memory/notes/{nid}?user_id=harald',
                     headers=user_headers, json={'body': 'new'})
    assert r.status_code == 200
    assert r.get_json()['body'] == 'new'


def test_patch_audit_rejected(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_audit(
            user_id='harald', app='bt', provider='claude',
            chat_request_id='r1', prompt='', response='', tokens={},
            cost_eur=0, latency_ms=0, timestamp=None)
        nid = n.id
    r = client.patch(f'/memory/notes/{nid}?user_id=harald',
                     headers=user_headers, json={'body': 'tampering'})
    assert r.status_code == 403


def test_delete_soft_deletes(client, user_headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='gone',
                                      body='', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.delete(f'/memory/notes/{nid}?user_id=harald', headers=user_headers)
    assert r.status_code == 204
    with app.app_context():
        assert MemoryNote.query.get(nid).deleted_at is not None


def test_create_note_requires_auth(client):
    r = client.post('/memory/notes', json={'user_id': 'x', 'app': 'a',
                                            'title': 't', 'body': '', 'tags': []})
    assert r.status_code == 401


def test_body_size_limit(client, user_headers):
    big = 'x' * (1024 * 1024 + 10)
    r = client.post('/memory/notes', headers=user_headers,
                    json={'user_id': 'h', 'app': 'a', 'title': 'big',
                          'body': big, 'tags': []})
    assert r.status_code == 413
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_api_notes.py -v`
Expected: FAIL — `404 Not Found` (blueprint not registered).

- [ ] **Step 3: Implement `api/memory_api.py` (notes section only — events/audit/summarize added in T8/T9)**

```python
"""Memory API — notes/events/audit/summarize/list endpoints.

Auth: all endpoints require a Bearer token. User scoping uses _asserted_user_id
from api.auth. Admin token may pass ?user= to read other users' notes; never
to write on their behalf.
"""

from __future__ import annotations
from flask import Blueprint, request, jsonify, g
from sqlalchemy import or_
from database import db
from config import Config
from api.auth import require_token, _asserted_user_id
from storage.memory_models import MemoryNote, MemoryKind
from storage.memory import MemoryWriter, NoteAlreadyExists
from storage.vault_renderer import VaultRenderer

memory_bp = Blueprint('memory', __name__, url_prefix='/memory')

_BODY_MAX = 1024 * 1024  # 1 MiB


def _gate():
    if not Config.MEMORY_ENABLED:
        return jsonify({'error': 'memory feature disabled'}), 503
    return None


def _scope_user_id() -> str:
    """For non-admin tokens, force user_id to principal's user_id.
    Admin tokens may override via ?user=."""
    if g.principal.role == 'admin':
        return request.args.get('user') or _asserted_user_id() or g.principal.user_id
    return g.principal.user_id


@memory_bp.post('/notes')
@require_token
def create_note():
    gate = _gate()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    user_id = _scope_user_id()
    if g.principal.role != 'admin' and body.get('user_id') and body['user_id'] != user_id:
        return jsonify({'error': 'cross-user write forbidden'}), 403

    text_body = body.get('body', '') or ''
    if len(text_body.encode('utf-8')) > _BODY_MAX:
        return jsonify({'error': 'body too large'}), 413

    try:
        note = MemoryWriter().write_note(
            user_id=user_id,
            app=body.get('app') or 'gateway',
            title=body.get('title') or '',
            body=text_body,
            tags=body.get('tags') or [],
            folder=body.get('folder'),
            slug=body.get('slug'),
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        VaultRenderer().render_one(note)
        render_pending = False
    except Exception:
        render_pending = True

    return jsonify({'id': note.id,
                    'path': f'{note.folder}/{note.slug}.md',
                    'render_pending': render_pending}), 201


@memory_bp.get('/notes')
@require_token
def list_notes():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.deleted_at.is_(None),
    )

    kind = request.args.get('kind')
    if kind:
        try:
            q = q.filter(MemoryNote.kind == MemoryKind(kind))
        except ValueError:
            return jsonify({'error': f'unknown kind: {kind}'}), 400

    if app_filter := request.args.get('app'):
        q = q.filter(MemoryNote.app == app_filter)
    if folder := request.args.get('folder'):
        q = q.filter(MemoryNote.folder == folder)
    if text := request.args.get('q'):
        pat = f'%{text}%'
        q = q.filter(or_(MemoryNote.title.like(pat), MemoryNote.body.like(pat)))

    try:
        limit = min(int(request.args.get('limit', '50')), 500)
        offset = max(int(request.args.get('offset', '0')), 0)
    except ValueError:
        return jsonify({'error': 'limit/offset must be integers'}), 400

    total = q.count()
    rows = (q.order_by(MemoryNote.created_at.desc())
              .limit(limit).offset(offset).all())
    return jsonify({'notes': [r.to_dict() for r in rows], 'total': total})


@memory_bp.get('/notes/<int:note_id>')
@require_token
def get_note(note_id: int):
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    n = MemoryNote.query.filter_by(id=note_id, user_id=user_id).first()
    if not n or n.deleted_at is not None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(n.to_dict())


@memory_bp.patch('/notes/<int:note_id>')
@require_token
def patch_note(note_id: int):
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    n = MemoryNote.query.filter_by(id=note_id, user_id=user_id).first()
    if not n or n.deleted_at is not None:
        return jsonify({'error': 'not found'}), 404
    if n.kind != MemoryKind.NOTE:
        return jsonify({'error': f'cannot edit kind={n.kind.value}'}), 403

    body = request.get_json(silent=True) or {}
    new_body = body.get('body')
    if new_body is not None:
        if len(new_body.encode('utf-8')) > _BODY_MAX:
            return jsonify({'error': 'body too large'}), 413
        n.body = new_body
    if 'title' in body:
        n.title = body['title'] or ''
    if 'tags' in body:
        n.tags = body['tags'] or []
    db.session.commit()
    try:
        VaultRenderer().render_one(n)
    except Exception:
        pass
    return jsonify(n.to_dict())


@memory_bp.delete('/notes/<int:note_id>')
@require_token
def delete_note(note_id: int):
    gate = _gate()
    if gate:
        return gate
    from datetime import datetime, timezone
    user_id = _scope_user_id()
    n = MemoryNote.query.filter_by(id=note_id, user_id=user_id).first()
    if not n or n.deleted_at is not None:
        return jsonify({'error': 'not found'}), 404
    n.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    try:
        VaultRenderer().cleanup_deleted()
    except Exception:
        pass
    return ('', 204)
```

- [ ] **Step 4: Register blueprint and enable MEMORY for tests**

In `app.py`, after the existing blueprint imports (around line 54), add:

```python
    from api.memory_api import memory_bp
```

And after the existing `app.register_blueprint(...)` lines (around line 65):

```python
    app.register_blueprint(memory_bp)
```

In `tests/conftest.py`, enable memory in the test app — modify the `app` fixture (right after the `Config.SERVICE_TOKEN = 'test-token'` line):

```python
    Config.MEMORY_ENABLED = True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_memory_api_notes.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Confirm full suite green**

Run: `pytest -q`
Expected: 174 prior + 9 = 183 passing.

- [ ] **Step 7: Commit**

```bash
git add api/memory_api.py app.py tests/conftest.py tests/test_memory_api_notes.py
git commit -m "$(cat <<'EOF'
Add: Memory API blueprint with notes CRUD

POST/GET/PATCH/DELETE under /memory/notes with strict per-user scoping.
Admin tokens may read across users via ?user=; writes are always scoped
to the principal. Body capped at 1 MiB.

Verified: pytest tests/test_memory_api_notes.py ✓ (9/9), full suite ✓

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8 — Memory API: events endpoints

**Tag:** opencode (extension of T7 pattern)
**Files:**
- Modify: `api/memory_api.py` (append endpoints)
- Test: `tests/test_memory_api_events.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_memory_api_events.py`:

```python
"""Memory API — events endpoints."""

import pytest
from config import Config


@pytest.fixture
def headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_create_event(client, headers):
    r = client.post('/memory/events', headers=headers,
                    json={'user_id': 'harald', 'app': 'bt',
                          'event_type': 'application_created',
                          'payload': {'company': 'ACME'}})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert 'application_created' in body['path']


def test_create_event_requires_type(client, headers):
    r = client.post('/memory/events', headers=headers,
                    json={'user_id': 'h', 'app': 'a', 'payload': {}})
    assert r.status_code == 400


def test_list_events_filter_by_type(client, headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        w = MemoryWriter()
        w.write_event(user_id='harald', app='bt', event_type='t1',
                      payload={}, tags=[], slug=None)
        w.write_event(user_id='harald', app='bt', event_type='t2',
                      payload={}, tags=[], slug=None)
    r = client.get('/memory/events?user_id=harald&event_type=t1', headers=headers)
    assert r.status_code == 200
    events = r.get_json()['events']
    assert len(events) == 1
    assert events[0]['extra']['event_type'] == 't1'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_memory_api_events.py -v`
Expected: FAIL — `404 Not Found`

- [ ] **Step 3: Append events endpoints to `api/memory_api.py`**

```python
@memory_bp.post('/events')
@require_token
def create_event():
    gate = _gate()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    user_id = _scope_user_id()
    if g.principal.role != 'admin' and body.get('user_id') and body['user_id'] != user_id:
        return jsonify({'error': 'cross-user write forbidden'}), 403
    if not body.get('event_type'):
        return jsonify({'error': 'event_type required'}), 400
    try:
        note = MemoryWriter().write_event(
            user_id=user_id,
            app=body.get('app') or 'gateway',
            event_type=body['event_type'],
            payload=body.get('payload') or {},
            tags=body.get('tags') or [],
            slug=body.get('slug'),
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    try:
        VaultRenderer().render_one(note)
        render_pending = False
    except Exception:
        render_pending = True
    return jsonify({'id': note.id,
                    'path': f'{note.folder}/{note.slug}.md',
                    'render_pending': render_pending}), 201


@memory_bp.get('/events')
@require_token
def list_events():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.kind == MemoryKind.EVENT,
        MemoryNote.deleted_at.is_(None),
    )
    if et := request.args.get('event_type'):
        q = q.filter(MemoryNote.folder.like(f'%/events/{et}'))
    if app_filter := request.args.get('app'):
        q = q.filter(MemoryNote.app == app_filter)
    try:
        limit = min(int(request.args.get('limit', '50')), 500)
    except ValueError:
        return jsonify({'error': 'limit must be integer'}), 400
    rows = q.order_by(MemoryNote.created_at.desc()).limit(limit).all()
    return jsonify({'events': [r.to_dict() for r in rows]})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_api_events.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add api/memory_api.py tests/test_memory_api_events.py
git commit -m "$(cat <<'EOF'
Add: events endpoints under /memory/events

POST creates kind=event notes with default JSON-payload template; GET
filters by event_type via folder-convention match (SQLite lacks JSON path
operators in scope for Phase 1).

Verified: pytest tests/test_memory_api_events.py ✓ (3/3)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9 — Memory API: audit + summarize endpoints

**Tag:** opencode
**Files:**
- Modify: `api/memory_api.py`
- Test: `tests/test_memory_api_audit.py`, `tests/test_memory_api_summarize.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_memory_api_audit.py`:

```python
"""Memory API — read-only audit listing."""

import pytest
from config import Config


@pytest.fixture
def headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_audit_lists_only_audit_kind(client, headers, app):
    with app.app_context():
        from storage.memory import MemoryWriter
        w = MemoryWriter()
        w.write_audit(user_id='harald', app='bt', provider='claude',
                      chat_request_id='r1', prompt='p', response='r',
                      tokens={}, cost_eur=0, latency_ms=0, timestamp=None)
        w.write_note(user_id='harald', app='bt', title='nope', body='',
                     tags=[], folder=None, slug=None)
    r = client.get('/memory/audit?user_id=harald', headers=headers)
    assert r.status_code == 200
    audit = r.get_json()['notes']
    assert all(n['kind'] == 'audit' for n in audit)
    assert len(audit) == 1
```

Create `tests/test_memory_api_summarize.py`:

```python
"""Memory API — on-demand per-note summarize endpoint."""

import pytest
from unittest.mock import patch
from config import Config


@pytest.fixture
def headers():
    return {'Authorization': f'Bearer {Config.SERVICE_TOKEN}'}


def test_summarize_creates_summary_note(client, headers, app, monkeypatch):
    monkeypatch.setattr(Config, 'MEMORY_FREE_MODELS', ['ollama::mistral'])
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt',
                                      title='Long doc', body='lorem ipsum...',
                                      tags=[], folder=None, slug=None)
        nid = n.id

    with patch('api.memory_api._call_summary_model', return_value=('Short.', 'ollama::mistral')):
        r = client.post(f'/memory/notes/{nid}/summarize?user_id=harald',
                        headers=headers, json={})
    assert r.status_code == 200, r.get_data(as_text=True)
    summary = r.get_json()['summary']
    assert summary['kind'] == 'summary'
    assert summary['extra']['source_ids'] == [nid]


def test_summarize_503_when_no_free_models(client, headers, app, monkeypatch):
    monkeypatch.setattr(Config, 'MEMORY_FREE_MODELS', [])
    with app.app_context():
        from storage.memory import MemoryWriter
        n = MemoryWriter().write_note(user_id='harald', app='bt', title='X',
                                      body='', tags=[], folder=None, slug=None)
        nid = n.id
    r = client.post(f'/memory/notes/{nid}/summarize?user_id=harald',
                    headers=headers, json={})
    assert r.status_code == 503
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_memory_api_audit.py tests/test_memory_api_summarize.py -v`
Expected: FAIL — endpoints not registered.

- [ ] **Step 3: Append audit + summarize endpoints to `api/memory_api.py`**

Add this import at the top of the file:
```python
from datetime import datetime, timezone
```

Append:

```python
@memory_bp.get('/audit')
@require_token
def list_audit():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.kind == MemoryKind.AUDIT,
        MemoryNote.deleted_at.is_(None),
    )
    if app_filter := request.args.get('app'):
        q = q.filter(MemoryNote.app == app_filter)
    try:
        limit = min(int(request.args.get('limit', '50')), 500)
    except ValueError:
        return jsonify({'error': 'limit must be integer'}), 400
    rows = q.order_by(MemoryNote.created_at.desc()).limit(limit).all()
    return jsonify({'notes': [r.to_dict() for r in rows]})


@memory_bp.get('/summaries')
@require_token
def list_summaries():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.kind == MemoryKind.SUMMARY,
        MemoryNote.deleted_at.is_(None),
    )
    if period := request.args.get('period'):
        kind_label, _, value = period.partition(':')
        if kind_label == 'day':
            q = q.filter(MemoryNote.folder == '_index/by-day',
                         MemoryNote.slug == value)
        elif kind_label == 'app':
            q = q.filter(MemoryNote.folder == '_index/by-app',
                         MemoryNote.slug == value)
        else:
            return jsonify({'error': f'unknown period: {period}'}), 400
    rows = q.order_by(MemoryNote.created_at.desc()).limit(200).all()
    return jsonify({'summaries': [r.to_dict() for r in rows]})


@memory_bp.post('/notes/<int:note_id>/summarize')
@require_token
def summarize_note(note_id: int):
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    n = MemoryNote.query.filter_by(id=note_id, user_id=user_id).first()
    if not n or n.deleted_at is not None:
        return jsonify({'error': 'not found'}), 404
    if not Config.MEMORY_FREE_MODELS:
        return jsonify({'error': 'no free models configured'}), 503
    try:
        summary_text, model_used = _call_summary_model(
            n.title, n.body, Config.MEMORY_FREE_MODELS,
        )
    except RuntimeError as e:
        return jsonify({'error': str(e)}), 503

    summary_note = MemoryWriter().write_summary(
        user_id=user_id, period=f'app:per-note-{n.id}',
        body=summary_text, source_ids=[n.id], model_used=model_used,
    )
    try:
        VaultRenderer().render_one(summary_note)
    except Exception:
        pass
    return jsonify({'summary': summary_note.to_dict()})


def _call_summary_model(title: str, body: str, models: list) -> tuple:
    """Call providers in order. Return (summary_text, model_used).

    Each entry is '<provider_id>::<model>'. Raises RuntimeError if all fail.
    """
    from dispatcher import _execute
    last_err = None
    prompt = (
        'Summarize the following note in 1-3 sentences. Respond with the summary only.\n\n'
        f'Title: {title}\n\n{body}'
    )
    messages = [{'role': 'user', 'content': prompt}]
    for spec in models:
        provider_id, _, model = spec.partition('::')
        if not provider_id or not model:
            continue
        try:
            result = _execute(
                user_id='__summary__', provider_id=provider_id, model=model,
                messages=messages, max_tokens=300,
                config_override={}, origin_app='memory-summarize',
            )
        except Exception as e:
            last_err = e
            continue
        text = ''
        if isinstance(result, dict):
            if isinstance(result.get('content'), str):
                text = result['content']
            elif isinstance(result.get('content'), list):
                text = '\n'.join(b.get('text', '') for b in result['content']
                                 if isinstance(b, dict))
        if text:
            return text.strip(), spec
        last_err = RuntimeError('empty response')
    raise RuntimeError(f'all free models failed: {last_err}')
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_memory_api_audit.py tests/test_memory_api_summarize.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Confirm full suite**

Run: `pytest -q`
Expected: prior + 3 = 189 passing.

- [ ] **Step 6: Commit**

```bash
git add api/memory_api.py tests/test_memory_api_audit.py tests/test_memory_api_summarize.py
git commit -m "$(cat <<'EOF'
Add: /memory/audit, /memory/summaries, /memory/notes/<id>/summarize

On-demand summarize calls the dispatcher with MEMORY_FREE_MODELS in order;
503 if none configured or all fail. Audit list is read-only filtered to
kind=audit.

Verified: pytest ✓

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10 — Vault export API

**Tag:** **claude-code** (path-traversal protection security-critical)
**Files:**
- Create: `api/vault_api.py`
- Modify: `app.py` — register blueprint
- Test: `tests/test_vault_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vault_api.py`:

```python
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
    assert all(n.startswith('harald/') for n in names if n)
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
    assert r.status_code in (400, 404)


def test_vault_missing_file_404(client, headers, vault_dir, app):
    r = client.get('/memory/vault/nothing/here.md?user_id=harald', headers=headers)
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vault_api.py -v`
Expected: FAIL — `404 Not Found` (blueprint missing).

- [ ] **Step 3: Implement `api/vault_api.py`**

```python
"""Vault export API — tarball of user's vault subtree + single-file download.

Path-traversal is enforced via Path.resolve().relative_to(root): the resolved
file path must be a descendant of VAULT_PATH/<user>. Refuses absolute paths
and `..` components before any filesystem call.
"""

from __future__ import annotations
import io
import os
import tarfile
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, g
from config import Config
from api.auth import require_token, _asserted_user_id

vault_bp = Blueprint('vault', __name__, url_prefix='/memory')


def _gate():
    if not Config.MEMORY_ENABLED:
        return jsonify({'error': 'memory feature disabled'}), 503
    return None


def _scope_user_id() -> str:
    if g.principal.role == 'admin':
        return request.args.get('user') or _asserted_user_id() or g.principal.user_id
    return g.principal.user_id


@vault_bp.get('/vault.tar.gz')
@require_token
def vault_tarball():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    root = Path(Config.VAULT_PATH) / user_id
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        if root.exists():
            t.add(str(root), arcname=user_id)
    buf.seek(0)
    return send_file(buf, mimetype='application/gzip',
                     as_attachment=True,
                     download_name=f'{user_id}-vault.tar.gz')


@vault_bp.get('/vault/<path:relpath>')
@require_token
def vault_file(relpath: str):
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    if not relpath or relpath.startswith('/') or '..' in relpath.split('/'):
        return jsonify({'error': 'invalid path'}), 400
    root = (Path(Config.VAULT_PATH) / user_id).resolve()
    candidate = (root / relpath).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return jsonify({'error': 'invalid path'}), 400
    if not candidate.exists() or not candidate.is_file():
        return jsonify({'error': 'not found'}), 404
    return send_file(str(candidate), mimetype='text/markdown')
```

- [ ] **Step 4: Register blueprint in `app.py`**

After the `from api.memory_api import memory_bp` line:
```python
    from api.vault_api import vault_bp
```

After `app.register_blueprint(memory_bp)`:
```python
    app.register_blueprint(vault_bp)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_vault_api.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add api/vault_api.py app.py tests/test_vault_api.py
git commit -m "$(cat <<'EOF'
Add: vault export API (tar.gz + single file)

Path-traversal guarded via Path.resolve().relative_to(root) check. Rejects
absolute paths and '..' before any filesystem call. Cross-user reads
require admin token.

Verified: pytest tests/test_vault_api.py ✓ (5/5)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11 — SummaryJob + CLI

**Tag:** opencode
**Files:**
- Create: `agents/__init__.py` (empty)
- Create: `agents/summary_job.py`
- Modify: `cli.py` — add `summary-job` click command
- Modify: `app.py` — register CLI command
- Test: `tests/test_summary_job.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_summary_job.py`:

```python
"""SummaryJob runner — nightly per-day and per-app aggregates."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind, SummaryJob


@pytest.fixture
def free_models(monkeypatch):
    monkeypatch.setattr(Config, 'MEMORY_FREE_MODELS', ['ollama::mistral'])


def _seed_audit(user_id, app_name, when, n=3):
    from storage.memory import MemoryWriter
    w = MemoryWriter()
    for i in range(n):
        w.write_audit(user_id=user_id, app=app_name, provider='claude',
                      chat_request_id=f'{user_id}-{app_name}-{when.date()}-{i}',
                      prompt=f'p{i}', response=f'r{i}',
                      tokens={}, cost_eur=0, latency_ms=10, timestamp=when)


def test_run_for_day_creates_summary(app, free_models):
    with app.app_context():
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        _seed_audit('harald', 'bt', yesterday, n=2)
        with patch('agents.summary_job._call_model',
                   return_value=('Yesterday harald had 2 audits.', 'ollama::mistral')):
            from agents.summary_job import run_for_day
            jobs = run_for_day(yesterday.date())
        assert jobs[0].status == 'completed'
        summaries = MemoryNote.query.filter_by(kind=MemoryKind.SUMMARY,
                                                user_id='harald').all()
        assert len(summaries) >= 1


def test_run_for_day_skips_user_with_no_audit(app, free_models):
    with app.app_context():
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        from agents.summary_job import run_for_day
        jobs = run_for_day(yesterday)
        assert jobs == []


def test_run_for_day_handles_llm_failure(app, free_models):
    with app.app_context():
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        _seed_audit('u1', 'bt', yesterday, n=1)
        with patch('agents.summary_job._call_model',
                   side_effect=RuntimeError('all models failed')):
            from agents.summary_job import run_for_day
            jobs = run_for_day(yesterday.date())
        assert jobs[0].status == 'failed'
        assert 'failed' in jobs[0].error_msg.lower()


def test_run_for_day_respects_max_notes_cap(app, free_models, monkeypatch):
    monkeypatch.setattr(Config, 'SUMMARY_MAX_NOTES_PER_DAY', 2)
    with app.app_context():
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        _seed_audit('u', 'bt', yesterday, n=5)
        with patch('agents.summary_job._call_model') as mock:
            from agents.summary_job import run_for_day
            jobs = run_for_day(yesterday.date())
        assert mock.call_count == 0
        assert jobs[0].status == 'completed'
        sums = MemoryNote.query.filter_by(kind=MemoryKind.SUMMARY, user_id='u').all()
        assert any('skipped' in (s.body or '').lower() for s in sums)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_summary_job.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agents'`

- [ ] **Step 3: Create `agents/__init__.py`**

Empty file:

```python
"""Background agents (summary job, etc.)."""
```

- [ ] **Step 4: Implement `agents/summary_job.py`**

```python
"""Nightly summary job — produces by-day and by-app aggregates.

Driven from `flask summary-job run`. Calls Config.MEMORY_FREE_MODELS in
order (cheap-first); failure of all models marks the job failed and does
not fall back to paid providers (cost control).
"""

from __future__ import annotations
import logging
from datetime import date, datetime, time, timezone, timedelta
from typing import Iterable, Tuple
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind, SummaryJob
from storage.memory import MemoryWriter
from storage.vault_renderer import VaultRenderer

logger = logging.getLogger(__name__)


def run_for_day(target: date) -> list[SummaryJob]:
    """Aggregate per-user audit notes for the given calendar day (UTC).
    Returns one SummaryJob per user touched."""
    start = datetime.combine(target, time.min).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    user_ids = [
        row[0] for row in
        db.session.query(MemoryNote.user_id).filter(
            MemoryNote.kind == MemoryKind.AUDIT,
            MemoryNote.created_at >= start,
            MemoryNote.created_at < end,
            MemoryNote.deleted_at.is_(None),
        ).distinct().all()
    ]

    out = []
    for uid in user_ids:
        out.append(_run_one(period=f'day:{target.isoformat()}', user_id=uid,
                             start=start, end=end))
    return out


def run_for_app(target_app: str) -> list[SummaryJob]:
    """Aggregate per-(user, app) over the last 30 days. Returns one SummaryJob
    per user with audits for that app."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    user_ids = [
        row[0] for row in
        db.session.query(MemoryNote.user_id).filter(
            MemoryNote.kind == MemoryKind.AUDIT,
            MemoryNote.app == target_app,
            MemoryNote.created_at >= start,
            MemoryNote.created_at < end,
            MemoryNote.deleted_at.is_(None),
        ).distinct().all()
    ]
    out = []
    for uid in user_ids:
        out.append(_run_one(period=f'app:{target_app}', user_id=uid,
                             start=start, end=end, app_filter=target_app))
    return out


def _run_one(*, period: str, user_id: str, start: datetime, end: datetime,
             app_filter: str | None = None) -> SummaryJob:
    job = SummaryJob(period=period, user_id=user_id, status='running',
                     started_at=datetime.now(timezone.utc))
    db.session.add(job)
    db.session.commit()

    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.kind == MemoryKind.AUDIT,
        MemoryNote.created_at >= start,
        MemoryNote.created_at < end,
        MemoryNote.deleted_at.is_(None),
    )
    if app_filter:
        q = q.filter(MemoryNote.app == app_filter)
    notes = q.order_by(MemoryNote.created_at).all()
    cap = Config.SUMMARY_MAX_NOTES_PER_DAY

    try:
        if len(notes) > cap:
            body = (f'Skipped LLM summarization: {len(notes)} notes exceed cap of {cap}. '
                    f'Source ids: {[n.id for n in notes[:10]]}...')
            model_used = 'none'
        else:
            structured = _structure_notes(notes)
            if not Config.MEMORY_FREE_MODELS:
                raise RuntimeError('no free models configured')
            body, model_used = _call_model(period, structured, Config.MEMORY_FREE_MODELS)

        summary = MemoryWriter().write_summary(
            user_id=user_id, period=period, body=body,
            source_ids=[n.id for n in notes], model_used=model_used,
        )
        try:
            VaultRenderer().render_one(summary)
        except Exception as e:
            logger.warning(f'summary render failed: {e}')

        job.status = 'completed'
        job.model_used = model_used
    except Exception as e:
        job.status = 'failed'
        job.error_msg = f'{type(e).__name__}: {e}'[:1000]
    finally:
        job.finished_at = datetime.now(timezone.utc)
        db.session.commit()
    return job


def _structure_notes(notes: Iterable[MemoryNote]) -> str:
    lines = []
    for n in notes:
        ts = n.created_at.isoformat()
        prov = (n.extra or {}).get('provider', '?')
        title = n.title or '(no title)'
        excerpt = (n.body or '')[:300].replace('\n', ' ')
        lines.append(f'- [{ts}] {n.app}/{prov}: {title} — {excerpt}')
    return '\n'.join(lines)


def _call_model(period: str, structured: str, models: list) -> Tuple[str, str]:
    """Call providers in order; return (summary_text, model_id_used).
    Raises RuntimeError if all fail."""
    from dispatcher import _execute
    prompt = (
        f'Summarize these chat events for {period}. '
        f'Return 5-10 sentences highlighting topics, decisions, and any unusual activity.\n\n'
        + structured
    )
    messages = [{'role': 'user', 'content': prompt}]
    last_err = None
    for spec in models:
        provider_id, _, model = spec.partition('::')
        if not provider_id or not model:
            continue
        try:
            result = _execute(
                user_id='__summary__', provider_id=provider_id, model=model,
                messages=messages, max_tokens=600,
                config_override={}, origin_app='memory-summarize',
            )
        except Exception as e:
            last_err = e
            continue
        text = ''
        if isinstance(result, dict):
            if isinstance(result.get('content'), str):
                text = result['content']
            elif isinstance(result.get('content'), list):
                text = '\n'.join(b.get('text', '') for b in result['content']
                                 if isinstance(b, dict))
        if text:
            return text.strip(), spec
        last_err = RuntimeError('empty response')
    raise RuntimeError(f'all free models failed: {last_err}')
```

- [ ] **Step 5: Add `summary-job` click command to `cli.py`**

Append at the end of `cli.py`:

```python
@click.command('summary-job')
@click.option('--period', default='day', type=click.Choice(['day', 'app']),
              help='Aggregate by day or by app.')
@click.option('--date', 'date_str', default=None,
              help='Target date (YYYY-MM-DD); for --period=day. Defaults to yesterday.')
@click.option('--app', 'app_name', default=None,
              help='App name; required for --period=app.')
@click.option('--yesterday', is_flag=True, help='Shortcut for --date=<yesterday>.')
def summary_job_command(period, date_str, app_name, yesterday):
    """Run summarization for a calendar day or for an app's last 30 days."""
    from datetime import date, datetime, timedelta, timezone
    from agents.summary_job import run_for_day, run_for_app

    if period == 'day':
        if yesterday or not date_str:
            target = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        else:
            target = date.fromisoformat(date_str)
        jobs = run_for_day(target)
        click.echo(f'Ran {len(jobs)} summary jobs for {target}.')
        for j in jobs:
            click.echo(f'  {j.user_id}: {j.status} (model={j.model_used or "-"})')
    else:
        if not app_name:
            click.echo('--app=<name> required for --period=app', err=True)
            raise click.Abort()
        jobs = run_for_app(app_name)
        click.echo(f'Ran {len(jobs)} summary jobs for app {app_name}.')
        for j in jobs:
            click.echo(f'  {j.user_id}: {j.status} (model={j.model_used or "-"})')
```

- [ ] **Step 6: Register the CLI command in `app.py`**

In `app.py`, extend the existing import line for cli commands:

```python
    from cli import grants_bootstrap_command, update_opencode_pricing_command, summary_job_command
    app.cli.add_command(grants_bootstrap_command)
    app.cli.add_command(update_opencode_pricing_command)
    app.cli.add_command(summary_job_command)
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_summary_job.py -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Commit**

```bash
git add agents/ cli.py app.py tests/test_summary_job.py
git commit -m "$(cat <<'EOF'
Add: SummaryJob + 'flask summary-job' CLI command

Nightly per-user per-day aggregation via Config.MEMORY_FREE_MODELS.
Honors SUMMARY_MAX_NOTES_PER_DAY cap by writing a stub summary instead
of paying for big LLM calls.

Verified: pytest tests/test_summary_job.py ✓ (4/4)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12 — Self-heal cron + CLI

**Tag:** opencode
**Files:**
- Modify: `cli.py` — add `vault-render` click command
- Modify: `app.py` — register CLI command
- Test: `tests/test_vault_render_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_vault_render_cli.py`:

```python
"""flask vault-render CLI — full rebuild and stale-check."""

import os
import pytest
from pathlib import Path
from config import Config


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    p = tmp_path / 'vault'
    p.mkdir()
    monkeypatch.setattr(Config, 'VAULT_PATH', str(p))
    return p


def test_vault_render_full_rebuild(app, vault_dir):
    runner = app.test_cli_runner()
    with app.app_context():
        from storage.memory import MemoryWriter
        MemoryWriter().write_note(user_id='u', app='a', title='one',
                                   body='', tags=[], folder=None, slug=None)
    result = runner.invoke(args=['vault-render', '--rebuild'])
    assert result.exit_code == 0
    assert 'rendered' in result.output.lower()
    assert (vault_dir / 'u' / 'a' / 'notes' / 'one.md').exists()


def test_vault_render_check_stale(app, vault_dir):
    runner = app.test_cli_runner()
    with app.app_context():
        from storage.memory import MemoryWriter
        from storage.vault_renderer import VaultRenderer
        n = MemoryWriter().write_note(user_id='u', app='a', title='one',
                                       body='v1', tags=[], folder=None, slug=None)
        VaultRenderer().render_one(n)
        (vault_dir / 'u' / 'a' / 'notes' / 'one.md').unlink()
    result = runner.invoke(args=['vault-render', '--check-stale'])
    assert result.exit_code == 0
    assert (vault_dir / 'u' / 'a' / 'notes' / 'one.md').exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vault_render_cli.py -v`
Expected: FAIL — `vault-render` command not found.

- [ ] **Step 3: Add the click command to `cli.py`**

Append:

```python
@click.command('vault-render')
@click.option('--rebuild', is_flag=True, help='Re-render every live note.')
@click.option('--check-stale', 'check_stale', is_flag=True,
              help='Only re-render notes whose DB row is newer than the file (or missing).')
@click.option('--user', default=None, help='Restrict to one user (with --rebuild).')
def vault_render_command(rebuild, check_stale, user):
    """Render or repair the filesystem vault from the database."""
    from storage.vault_renderer import VaultRenderer
    r = VaultRenderer()
    if rebuild:
        n = r.rebuild_all(user_id=user)
        click.echo(f'rendered {n} notes')
    elif check_stale:
        n = r.check_stale()
        removed = r.cleanup_deleted()
        click.echo(f'rendered {n} stale notes; cleaned up {removed} deleted')
    else:
        click.echo('pass --rebuild or --check-stale', err=True)
        raise click.Abort()
```

- [ ] **Step 4: Register in `app.py`**

Extend the cli import:

```python
    from cli import (grants_bootstrap_command, update_opencode_pricing_command,
                     summary_job_command, vault_render_command)
    app.cli.add_command(grants_bootstrap_command)
    app.cli.add_command(update_opencode_pricing_command)
    app.cli.add_command(summary_job_command)
    app.cli.add_command(vault_render_command)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_vault_render_cli.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Confirm full suite still green**

Run: `pytest -q`
Expected: prior + 6 (summary + render-cli) ≈ 197 passing.

- [ ] **Step 7: Commit**

```bash
git add cli.py app.py tests/test_vault_render_cli.py
git commit -m "$(cat <<'EOF'
Add: 'flask vault-render' CLI (--rebuild / --check-stale)

Powers the self-heal systemd timer. --check-stale re-renders DB rows
newer than their .md file (or whose file is missing) and removes
orphaned files for soft-deleted notes.

Verified: pytest tests/test_vault_render_cli.py ✓ (2/2), full suite ✓

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13 — systemd units

**Tag:** **claude-code** (VPS deploy, SELinux)
**Files:**
- Create: `deploy/ai-provider-summary.service`
- Create: `deploy/ai-provider-summary.timer`
- Create: `deploy/ai-provider-vault-render.service`
- Create: `deploy/ai-provider-vault-render.timer`

No automated tests — these are deploy artifacts. Manual verification on VPS only.

- [ ] **Step 1: Create `deploy/ai-provider-summary.service`**

```ini
[Unit]
Description=ai-provider-service nightly summary job
After=ai-provider-service.service
Requires=ai-provider-service.service

[Service]
Type=oneshot
User=ai-provider
Group=ai-provider
WorkingDirectory=/opt/ai-provider-service
EnvironmentFile=/etc/ai-provider-service/.env
ExecStart=/opt/ai-provider-service/venv/bin/flask --app app summary-job --period=day --yesterday
StandardOutput=append:/var/log/ai-provider-service/summary-job.log
StandardError=inherit
```

- [ ] **Step 2: Create `deploy/ai-provider-summary.timer`**

```ini
[Unit]
Description=ai-provider-service nightly summary job timer

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
Unit=ai-provider-summary.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create `deploy/ai-provider-vault-render.service`**

```ini
[Unit]
Description=ai-provider-service vault self-heal
After=ai-provider-service.service
Requires=ai-provider-service.service

[Service]
Type=oneshot
User=ai-provider
Group=ai-provider
WorkingDirectory=/opt/ai-provider-service
EnvironmentFile=/etc/ai-provider-service/.env
ExecStart=/opt/ai-provider-service/venv/bin/flask --app app vault-render --check-stale
StandardOutput=append:/var/log/ai-provider-service/vault-render.log
StandardError=inherit
```

- [ ] **Step 4: Create `deploy/ai-provider-vault-render.timer`**

```ini
[Unit]
Description=ai-provider-service vault self-heal timer

[Timer]
OnUnitActiveSec=10min
OnBootSec=2min
Unit=ai-provider-vault-render.service

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Smoke-test on VPS (manual, NOT in this commit)**

Do **not** deploy as part of this PR. Document the steps for a later manual deploy in OPERATIONS.md (Task 14).

```bash
sudo install -d -o ai-provider -g ai-provider -m 0750 /var/lib/ai-provider-service/vault
sudo semanage fcontext -a -t var_lib_t '/var/lib/ai-provider-service/vault(/.*)?'
sudo restorecon -Rv /var/lib/ai-provider-service/vault
sudo systemctl daemon-reload
sudo systemctl enable --now ai-provider-summary.timer ai-provider-vault-render.timer
sudo systemctl list-timers | grep ai-provider
```

- [ ] **Step 6: Commit**

```bash
git add deploy/ai-provider-summary.service deploy/ai-provider-summary.timer \
        deploy/ai-provider-vault-render.service deploy/ai-provider-vault-render.timer
git commit -m "$(cat <<'EOF'
Add: systemd timer + service units for memory background jobs

Two oneshot units: ai-provider-summary (nightly @ 02:30 UTC) and
ai-provider-vault-render (every 10 min). NOT deployed yet; OPERATIONS.md
in next commit documents the VPS rollout.

Verified: file-level only; not deployed to VPS.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14 — Documentation sync (AGENTS.md §5.1)

**Tag:** **claude-code**
**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `OPERATIONS.md`

- [ ] **Step 1: Update `AGENTS.md`**

Add to §3 (Hard rules) after §3.5:

```markdown
### 3.6 Markdown memory vault is rendered, not authored

- `VAULT_PATH` (default `/var/lib/ai-provider-service/vault`) contains `.md` files **generated from the DB** by `VaultRenderer`. Treat as cache.
- DB tables `memory_notes` and `summary_jobs` are the source of truth.
- ✅ Edit notes via `PATCH /memory/notes/<id>`.
- ❌ Hand-edit `.md` files under `VAULT_PATH` — the next self-heal cron will overwrite them.
- ❌ Reference a hardcoded vault path; read `Config.VAULT_PATH` (mirrors §3.2 SQLite rule).
```

Extend §6 (Production access reference) table with two rows:

```markdown
| Vault path | `/var/lib/ai-provider-service/vault/<user>/...` (cache; regen via `flask vault-render --rebuild`) |
| Timers | `systemctl list-timers \| grep ai-provider` — summary @ 02:30 UTC, vault-render every 10 min |
```

- [ ] **Step 2: Update `README.md`**

Add a "Markdown memory" section after the existing setup/env section:

````markdown
## Markdown memory (Phase 1)

Per-user audit + app-written notes are persisted in the DB and rendered as
`.md` files under `VAULT_PATH`. Open the vault in Obsidian or rsync it down
via `GET /memory/vault.tar.gz`.

**Required env vars** (`.env`):

```
MEMORY_ENABLED=true
VAULT_PATH=/var/lib/ai-provider-service/vault
SUMMARY_PROFILE=cheap-first
SUMMARY_MAX_NOTES_PER_DAY=200
MEMORY_FREE_MODELS=ollama::mistral,opencode::deepseek-v4-flash-free
```

Source of truth is SQLite. The vault directory is a regenerable cache —
do not back it up, do not edit files directly. See
`docs/superpowers/specs/2026-06-05-markdown-memory-design.md` for the
design rationale.

CLI:
- `flask summary-job --period=day --yesterday` — nightly aggregate
- `flask vault-render --rebuild` — full re-render from DB
- `flask vault-render --check-stale` — self-heal cron entrypoint
````

- [ ] **Step 3: Update `OPERATIONS.md`**

Append a section:

````markdown
## Markdown memory vault

**Source of truth:** SQLite (`memory_notes` and `summary_jobs` tables).

**Filesystem view:** `/var/lib/ai-provider-service/vault/` — regenerable
cache. *Excluded* from backups by design; if you lose it, run
`flask vault-render --rebuild` to recreate from DB.

**Background timers:**
- `ai-provider-summary.timer` — nightly @ 02:30 UTC. Logs:
  `/var/log/ai-provider-service/summary-job.log`.
- `ai-provider-vault-render.timer` — every 10 min. Logs:
  `/var/log/ai-provider-service/vault-render.log`.

**Feature flag:** `MEMORY_ENABLED` in `/etc/ai-provider-service/.env`.
Set to `false` to disable audit hook and put Memory API into 503 mode
without restarting.

**Disaster recovery for the vault directory:**

```bash
sudo rm -rf /var/lib/ai-provider-service/vault/*
sudo -u ai-provider /opt/ai-provider-service/venv/bin/flask --app app vault-render --rebuild
```

**Deploy of Phase 1 (one-time):**

```bash
# 1. Pull branch, install unit files
sudo cp deploy/ai-provider-summary.{service,timer} /etc/systemd/system/
sudo cp deploy/ai-provider-vault-render.{service,timer} /etc/systemd/system/

# 2. Create vault dir + SELinux context
sudo install -d -o ai-provider -g ai-provider -m 0750 /var/lib/ai-provider-service/vault
sudo semanage fcontext -a -t var_lib_t '/var/lib/ai-provider-service/vault(/.*)?'
sudo restorecon -Rv /var/lib/ai-provider-service/vault

# 3. Update .env (add MEMORY_ENABLED=true and the other keys)

# 4. Restart service + enable timers
sudo systemctl restart ai-provider-service
sudo systemctl daemon-reload
sudo systemctl enable --now ai-provider-summary.timer ai-provider-vault-render.timer
sudo systemctl list-timers | grep ai-provider
```
````

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md README.md OPERATIONS.md
git commit -m "$(cat <<'EOF'
Doc: AGENTS/README/OPERATIONS updates for markdown memory

Hard rule §3.6 (vault is rendered, not authored), production reference
table extended, README setup section, OPERATIONS deploy + DR sections.
Per AGENTS.md §5.1 sync discipline.

Verified: doc-only.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Run full suite**

```bash
pytest -q
```

Expected: 142 prior + ~57 new memory-related tests = ~199 passing.

- [ ] **Smoke-test the dispatcher with memory enabled (local, not VPS)**

```bash
export MEMORY_ENABLED=true
export VAULT_PATH=./vault
export SERVICE_TOKEN=devtoken
export MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
flask --app app run --port 8767 &

curl -s -H "Authorization: Bearer devtoken" \
     -H "Content-Type: application/json" \
     -d '{"user_id":"harald","app":"bt","title":"Hello","body":"World","tags":[]}' \
     http://localhost:8767/memory/notes

ls ./vault/harald/bt/notes/
cat ./vault/harald/bt/notes/hello.md
```

Expected: 201 from POST, file rendered with frontmatter.

- [ ] **Open a PR for `feat/memory-phase1` against `main`**

```bash
git push -u origin feat/memory-phase1
gh pr create --title "feat: markdown memory Phase 1 (audit + app-writes + nightly aggregates)" --body "$(cat <<'EOF'
## Summary

Phase-1 implementation of the markdown memory layer per
`docs/superpowers/specs/2026-06-05-markdown-memory-design.md`:

- New polymorphic `memory_notes` table + `summary_jobs`
- `MemoryWriter` + `VaultRenderer` (DB → `.md` files under `VAULT_PATH`)
- Dispatcher audit hook (gated by `MEMORY_ENABLED`)
- `/memory/notes`, `/memory/events`, `/memory/audit`, `/memory/summaries`, `/memory/notes/<id>/summarize`
- `/memory/vault.tar.gz` + `/memory/vault/<path>` with path-traversal guard
- `flask summary-job` (nightly aggregate) + `flask vault-render` (self-heal)
- systemd timer units for the two cron jobs
- Docs synced (AGENTS.md §3.6 + §6, README, OPERATIONS)

Prompt injection is **out of scope** for Phase 1.

## Test plan

- [x] `pytest -q` ✓ (~199 tests)
- [ ] Local smoke: curl `/memory/notes`, check `.md` rendered
- [ ] VPS deploy per `OPERATIONS.md` (separate task, NOT in this PR's merge)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Deferred from this PR (acknowledged gaps)

These items appear in the spec but are intentionally *not* in this plan to keep scope tight. Track as a follow-up:

- **Rate limiting** (spec §API surface): 60 POST/min per user, 5/min vault export. In-process sliding-window counter. Deferring because (a) no abusive callers exist today, (b) limits are easier to tune once we see real traffic shapes, (c) implementation is isolated and lands cleanly as a small follow-up PR.
- **Self-recursive audit by SummaryJob**: every summary LLM call goes through `dispatcher._execute`, which writes its own audit row tagged `app='memory-summarize'`. This is intentional (full traceability of summary calls) and harmless because the next day's summary scope excludes today's summary-calls by time window. Document this in `OPERATIONS.md` under "expected audit chatter".

## Handing off to opencode

Tell opencode literally:

> "Execute the plan at `docs/superpowers/plans/2026-06-05-markdown-memory-phase1.md` in this worktree. Run only the tasks tagged `opencode`. Stop and report back when you hit a `claude-code` task — I'll take those manually. Commit per task, do not squash. Run `pytest -q` after each task."

Tasks for opencode (in order): **1, 2, 3, 4, 5, 8, 9, 11, 12**.
Tasks reserved for Claude Code: **6, 7, 10, 13, 14**.

When opencode is done with its tasks, the worktree comes back with commits for the opencode tasks. Then Claude Code does the security-sensitive + deploy + docs tasks in order, opening the PR at the end.
