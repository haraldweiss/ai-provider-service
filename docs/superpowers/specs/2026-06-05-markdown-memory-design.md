# Markdown Memory — Design Spec

**Status:** Draft — awaiting user review
**Date:** 2026-06-05
**Author:** Brainstorming session, ai-provider-service
**Phase:** 1 (Storage layer — no prompt injection)

---

## Motivation

The ai-provider-service is today a pure gateway: it routes chat requests through providers with fallback, encrypts API keys, and persists a job queue. Consuming apps (Bewerbungstracker, loganonymizer) carry no shared memory layer — every request is stateless.

This spec adds a **per-user, app-aware Markdown memory layer** inspired by the Karpathy-Obsidian-wiki pattern: every interaction and every app-written note becomes a readable `.md` file under a vault directory. Apps gain a structured place to record state; the user gains a single browsable artifact (openable in Obsidian) covering everything that flows through the gateway for them.

Phase 1 is **storage-only**: audit + app-writes + LLM-generated aggregates. **Prompt injection is explicitly out of scope** and will be designed separately once the data layer is stable.

## Phase-1 Scope (Decisions)

The following decisions were locked in during brainstorming:

| Topic | Decision | Rationale |
|---|---|---|
| Purpose | **D4** — Audit + App-Writes, no Injection | Storage is the foundation; injection adds relevance/budget/privacy complexity better handled separately |
| Tenancy | **T3** — Per-user vault with app subfolders + `_shared/` | Single per-user memory while preserving per-app isolation |
| Storage | **S3** — SQLite as source of truth + rendered filesystem view | Transactional integrity + Obsidian-readable artifact |
| Audit content | **W1** — Full prompt + response | Lossless memory; trade-off: chat content now lives in two places (DB + FS) |
| Privacy | No Fernet on disk; document permissions/backup posture | Chat content is already plaintext in DB today; rule §3.1 covers API keys, not chat |
| Aggregation | **A3** — Raw notes + LLM-generated summaries | Full Karpathy-pattern: per-source / per-day aggregates produced by cheap model |
| Summary timing | **G4** — Nightly cron for `by-day`/`by-app` + on-demand per-note endpoint | Avoid LLM calls in the chat hot path; manual trigger available |
| Summary model | Reuse fallback chain with `cheap-first` profile | Leverages existing free-model-failover work |
| App-write API | **API3** — Both `POST /memory/notes` (free markdown) and `POST /memory/events` (typed) | Apps choose by use case; no forced migration |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  API-Layer (Flask blueprints)                               │
│  ├─ chat_api.py       (existing, calls Dispatcher)          │
│  ├─ memory_api.py     (NEW — Notes/Events CRUD + search)    │
│  └─ vault_api.py      (NEW — Filesystem export, tar/file)   │
└─────┬───────────────────────────────────────────────────────┘
      │
      ├─► Dispatcher (existing) ──► after_chat_hook ──► MemoryWriter
      │
      └─► MemoryWriter (NEW)
            │
            ├─► storage/memory_models.py (NEW — DB schema)
            └─► storage/vault_renderer.py (NEW — DB → .md files)

  Background (systemd timers):
    ai-provider-summary.timer       — nightly @ 02:30 UTC, "rebuild_aggregates"
    ai-provider-vault-render.timer  — every 10 min, "check-stale, self-heal"
    worker.py (existing queue)      — async VaultRender jobs from chat hook
```

### Components (isolated responsibilities)

1. **`MemoryWriter`** (`storage/memory.py`)
   - Single entry point for creating notes/events
   - Called from: dispatcher hook (audit), memory_api (app-writes), summary job
   - Validates inputs, performs DB insert, triggers (or enqueues) render

2. **`VaultRenderer`** (`storage/vault_renderer.py`)
   - Projects DB rows to `.md` files under `VAULT_PATH`
   - Idempotent — can fully rebuild vault from scratch
   - Triggered after-write (sync for app-writes, async for audit) and by self-heal cron

3. **`SummaryJob`** (`agents/summary_job.py`)
   - Nightly + on-demand
   - Reads raw notes via DB, calls dispatcher with `cheap-first` profile
   - Writes results as `kind=summary` rows; renderer produces aggregate files

### Hard rules respected (from AGENTS.md)

- §3.2 SQLite path from config — same pattern: `VAULT_PATH` from env, no hardcoding
- §3.4 Summary work is async/background, never blocks a chat request
- §3.5 systemd units own permissions; vault directory inherits service-user ownership
- §5.1 AGENTS.md / README / OPERATIONS.md updated in lockstep with the change

### Explicit non-goals (Phase 1)

- Prompt injection into chat requests
- Full-text search index (Phase 1 uses `LIKE %q%` on title/body — adequate for expected scale)
- Two-way Obsidian sync (Phase 1 is pull-only via tarball)
- Per-note encryption on disk
- Web UI for the vault (CLI + API only)
- Backfill of historical chat logs into audit notes

## Data Model

### Database schema

```python
# storage/memory_models.py

class MemoryNote(Base):
    __tablename__ = 'memory_notes'

    id: int                 # PK
    user_id: str            # vault owner
    app: str                # 'bewerbungstracker', 'loganonymizer', 'gateway' (audit)
    kind: Enum              # 'audit' | 'note' | 'event' | 'summary'
    folder: str             # 'audit/2026-06-05', 'notes', 'events/<type>', '_shared', '_index/by-day'
    slug: str               # filename without .md
    title: str
    body: str               # markdown body (no frontmatter — generated at render time)
    tags: JSON              # ['chat', 'job-search', ...]
    extra: JSON             # kind-specific metadata (see below)
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime    # soft delete

    __table_args__ = (
        UniqueConstraint('user_id', 'folder', 'slug'),
        Index('ix_memory_user_kind', 'user_id', 'kind'),
        Index('ix_memory_user_folder', 'user_id', 'folder'),
        Index('ix_memory_user_created', 'user_id', 'created_at'),
    )
```

`extra` contents by `kind`:
- `audit` — `{provider, chat_request_id, tokens: {prompt, completion}, cost_eur, latency_ms}`
- `note` — empty or arbitrary app metadata
- `event` — `{event_type, payload}`
- `summary` — `{period: 'day:YYYY-MM-DD' | 'app:<name>', source_ids: [int...], model: 'gemini-flash-free'}`

Additional unique partial index: `chat_request_id` UNIQUE WHERE NOT NULL — guarantees audit idempotency on dispatcher retries.

```python
class SummaryJob(Base):
    __tablename__ = 'summary_jobs'

    id: int
    period: str             # 'day:2026-06-05', 'app:bewerbungstracker'
    user_id: str
    status: Enum            # 'pending' | 'running' | 'completed' | 'failed'
    started_at: datetime
    finished_at: datetime
    error_msg: str
    model_used: str
```

### Vault layout on disk

```
/var/lib/ai-provider-service/vault/
└── <user>/
    ├── <app>/                                       # T3: per-app subfolder
    │   ├── audit/
    │   │   └── 2026/06/05/
    │   │       └── 20260605T143211Z-req_abc12.md    # kind=audit
    │   ├── notes/
    │   │   └── meeting-summary-acme.md              # kind=note
    │   └── events/
    │       └── application_created/
    │           └── 20260605T143211Z-acme-corp.md    # kind=event
    ├── _shared/
    │   └── notes/
    │       └── projekt-fokus.md                     # cross-app
    └── _index/                                      # generated by SummaryJob
        ├── by-day/
        │   └── 2026-06-05.md
        └── by-app/
            └── bewerbungstracker.md
```

Path rules:
- App-writes without explicit `folder` → `<app>/notes/`
- Audit-writes → `<app>/audit/<YYYY>/<MM>/<DD>/`
- Events → `<app>/events/<event_type>/`
- `folder=_shared` → cross-app
- `_index/` is writable only by `SummaryJob` (renderer rejects other writers)

### Frontmatter format (rendered)

```markdown
---
id: 42
kind: audit
user: harald
app: bewerbungstracker
provider: claude
created: 2026-06-05T14:32:11Z
tags: [chat, audit]
chat_request_id: req_abc12
tokens: { prompt: 142, completion: 89 }
cost_eur: 0.003
latency_ms: 1842
---

## Prompt

…full user prompt…

## Response

…full model response…
```

App notes use the same structure with `kind: note` and any `extra` fields lifted into frontmatter.

### Slug generation

- **Audit** — deterministic: `{created_at_iso_compact}-{chat_request_id_prefix}` → `20260605T143211Z-req_abc12`
- **Note/Event** — `slugify(title)`. On collision: suffix `-2`, `-3`, … (max 100, then 409). Apps may supply explicit `slug` matching `^[a-z0-9-]{1,80}$`.

### Config

New env var: `VAULT_PATH` (default `/var/lib/ai-provider-service/vault`). Local dev: `./vault/`. Production: per deploy section below.

## API Surface

Mounted under `/memory/*` via new `api/memory_api.py` blueprint. Auth piggybacks on existing `api/auth.py`; every note is scoped to the authenticated `user_id`.

### Notes (free markdown)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/memory/notes` | Create — body: `{title, body, tags?, folder?, slug?}` → 201 `{id, path}` |
| `GET` | `/memory/notes` | List/search — `?app=&folder=&tag=&q=&kind=&from=&to=&limit=&offset=` → 200 `{notes, total}` |
| `GET` | `/memory/notes/<id>` | Single note (with rendered frontmatter) → 200 |
| `PATCH` | `/memory/notes/<id>` | Edit `title`/`body`/`tags` — only own `kind=note`, not audit/summary → 200 |
| `DELETE` | `/memory/notes/<id>` | Soft delete + vault file removal → 204 |

### Events (typed)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/memory/events` | Create — `{event_type, payload, tags?, slug?}` → 201 `{id, path}` |
| `GET` | `/memory/events` | List — `?event_type=&app=&from=&to=&limit=` → 200 |

Phase 1 event rendering: default template = frontmatter with `event_type` + payload as JSON code block. Custom per-type templates are Phase 2.

### Audit (read-only)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/memory/audit` | Same filters as `/memory/notes`, `kind=audit` fixed |

Audit is **not** written through the Memory API — only the dispatcher hook creates audit notes.

### Summaries

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/memory/notes/<id>/summarize` | On-demand per-note summary, sync, calls dispatcher with `cheap-first` profile → 200 `{summary}` |
| `GET` | `/memory/summaries` | List aggregates — `?period=day:2026-06-05` or `?period=app:bewerbungstracker` → 200 |

### Vault export

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/memory/vault.tar.gz` | Full vault tarball; user's subtree only |
| `GET` | `/memory/vault/<path>` | Single `.md` file; `os.path.commonpath` check enforces `VAULT_PATH/<user>` scope |

### Auth model

- Identity token resolved by `api/auth.py` → `user_id` (+ optional `app` claim)
- Every endpoint scopes to `user_id` automatically
- App filter comes from token claim where present, or explicit query param (admin tokens only)
- `ADMIN_TOKEN` may set `?user=<other>` for cross-user **read** only; never for writes on behalf of another user
- Provider-access gate (recently deployed) is **not** applied to Memory endpoints — these are not provider calls

### Rate limits & size limits

- Body limit per note: 1 MiB markdown
- POST rate limit per user: 60/min (in-process sliding window; no Redis added)
- Vault export rate limit: 5/min (disk IO protection)

### Explicit API exclusions

- No `POST /memory/audit` (dispatcher-only)
- No PATCH/DELETE for `kind=audit` or `kind=summary` (append-only integrity)
- No cross-user reads without admin token
- No WebSocket/SSE subscriptions (polling sufficient for Phase 1)
- No full-text search engine

## Data Flows

### Flow 1 — Chat with audit

```
App ──POST /chat/completions──► chat_api.py
                                    │
                                    ▼
                              dispatcher.dispatch()
                                    │
                                    ▼
                              provider.complete() ←── fallback chain
                                    │
                       ┌────────────┴────────────┐
                       ▼                         ▼
                Response → App           after_chat_hook (sync DB insert)
                                                 │
                                                 ▼
                                        MemoryWriter.write_audit({
                                          user, app, prompt, response,
                                          provider, tokens, cost, latency
                                        })
                                                 │
                              ┌──────────────────┴──────────────────┐
                              ▼                                      ▼
                       DB INSERT (sub-ms)                   enqueue VaultRender job
                                                                     │
                                                                     ▼
                                                            worker.py writes .md
```

Properties:
- DB insert is attempted synchronously **before** the response returns. INSERT is sub-millisecond, so on the happy path the audit row is durable by the time the chat response leaves the server.
- Filesystem render is async via existing `worker.py` queue.
- **Conflict-of-priorities rule:** if the audit INSERT fails (full disk, lock contention, etc.), the chat response still goes out — the chat must never fail because of an audit error. The failure is logged and `memory_audit_write_failures_total` increments. Accepted trade-off: under rare INSERT failure, that single audit row is lost. No retry queue for audit Phase 1; if this becomes a real loss vector in production, Phase 2 adds a fallback append-only log file.
- Idempotency via UNIQUE `chat_request_id` prevents double-audit on dispatcher retries.

### Flow 2 — App write

```
App ──POST /memory/notes──► memory_api.py
                                │
                                ▼
                          MemoryWriter.write_note()
                                │
                                ▼
                          DB INSERT (commit)
                                │
                                ▼
                      VaultRenderer.render_one() (inline, sync)
                                │
                                ▼
                          201 {id, path}
```

Render is **synchronous** for app writes — user expects file to be there immediately when they open Obsidian. Render failure: DB row persists, API returns 500 with `{id, render_pending: true}`. Self-heal cron resolves later.

### Flow 3 — Nightly aggregation

systemd timer (not crontab — service is already systemd-managed):

```
ai-provider-summary.timer  → ai-provider-summary.service
  OnCalendar=*-*-* 02:30:00
       │
       ▼
flask summary-job run --period=day --yesterday
       │
       ├─► for each user with audit notes from yesterday:
       │     - load all audit/notes
       │     - structure as (timestamp, app, title, body-excerpt) list
       │     - dispatcher.complete(profile="cheap-first")
       │     - persist as kind=summary, folder='_index/by-day', slug=YYYY-MM-DD
       │     - render .md with backlinks to all source_ids
       │
       └─► for each (user, app) pair with audit notes (rolling 30 days):
             - analogous, slug=<app>.md, folder='_index/by-app'
```

Properties:
- All cheap-first providers fail → job marked `failed`, retry next day; no fallback to expensive models (cost control)
- Status tracked in `summary_jobs` table
- Manual CLI: `flask summary-job run --user=harald --date=2026-06-05`

### Flow 4 — On-demand summary

```
App ──POST /memory/notes/<id>/summarize──► memory_api.py
                                                │
                                                ▼
                                          Load note from DB
                                                │
                                                ▼
                                  Dispatcher with cheap-first profile
                                                │
                                                ▼
                                    Create kind=summary note,
                                    extra.source_ids=[<id>]
                                                │
                                                ▼
                                          DB INSERT + render
                                                │
                                                ▼
                                          200 {summary: {id, body, ...}}
```

Synchronous (unlike Flow 3). Failure → 503 with Retry-After header.

### Flow 5 — Self-healing re-render

systemd timer every 10 min — `flask vault-render --check-stale`:

- Find all DB notes whose `updated_at` > vault file mtime (or file missing)
- Re-render them
- Garbage-collect vault files whose DB row is soft-deleted or absent

Filesystem view is **eventually consistent**; no hot path catches disk errors. Disaster recovery: delete vault directory, job rebuilds it.

## Error-handling matrix

| Failure | Where | Reaction |
|---|---|---|
| DB insert fail (audit) | Dispatcher hook | Log + counter, chat response still returns |
| DB insert fail (app write) | memory_api | 500, app may retry |
| Disk render fail | VaultRenderer | Log + flag in DB, self-heal cron retries |
| Summary LLM fail | SummaryJob | Job status=failed, no fallback to expensive models |
| Path-traversal attempt | vault/<path> route | 400 before any IO; `os.path.commonpath` check |
| Body > 1 MiB | memory_api | 413 Payload Too Large |
| Slug collision | MemoryWriter | Auto-suffix `-2`, `-3` (max 100, then 409) |
| User quota exceeded | memory_api | 429 (Phase 2; Phase 1 has no quotas) |

## Testing strategy

All `pytest`, building on the existing 142-test baseline.

### Unit tests (`tests/test_memory_*.py`)

- `MemoryWriter.write_audit` — DB row fields correct, idempotent on duplicate `chat_request_id`
- `MemoryWriter.write_note` — slugify, collision suffix, tag validation, folder whitelist
- `VaultRenderer.render_one` — frontmatter format, path computation per kind, markdown body escape
- `VaultRenderer.cleanup` — deleted rows remove vault files
- `Slug.from_title` — umlauts, special chars, empty input, too long
- Path-traversal — `vault/<path>` rejects `../`, absolute paths, symlink escape

### Integration tests (real temp SQLite + tempdir VAULT_PATH)

- End-to-end chat → audit note in DB + vault file (filesystem assert)
- `POST /memory/notes` → 201 + `GET /memory/notes/<id>` + `.md` file exists
- `POST /memory/events` → default template applied, frontmatter contains `event_type`
- `POST /memory/notes/<id>/summarize` with mocked dispatcher → `kind=summary` created, `extra.source_ids` correct
- `GET /memory/vault.tar.gz` → tarball contains only user subtree
- Self-heal — delete vault file manually, cron re-renders
- Auth — user A cannot read user B's notes (unless admin token)

### Cron job tests (no live LLM)

- `flask summary-job run --user=test --date=…` with mocked dispatcher
- Summary note has correct backlinks, `extra.model_used` set
- Failure path: dispatcher raises → job status `failed`, no crash

### Not tested in pytest

- Live LLM responses (test doubles for `dispatcher.complete()`)
- VPS SELinux behavior (deploy doc only)

## Migration

Single Alembic revision:

1. New table `memory_notes` (schema above)
2. New table `summary_jobs`
3. Indices listed above
4. Partial unique on `chat_request_id`

No backfill. Phase 1 starts empty. Historical chat/usage logs stay where they are.

## Deploy (VPS, Rocky 9)

Per AGENTS.md §6.

1. **Filesystem preparation**:
   ```bash
   sudo install -d -o ai-provider -g ai-provider -m 0750 /var/lib/ai-provider-service/vault
   sudo semanage fcontext -a -t var_lib_t '/var/lib/ai-provider-service/vault(/.*)?'
   sudo restorecon -Rv /var/lib/ai-provider-service/vault
   ```

2. **Env vars in `/etc/ai-provider-service/.env`**:
   ```
   VAULT_PATH=/var/lib/ai-provider-service/vault
   SUMMARY_PROFILE=cheap-first
   SUMMARY_MAX_NOTES_PER_DAY=200      # safety cap: skip summarization for a user/day if they exceed this many raw notes (default 200); skipped days get a stub summary marking the skip
   MEMORY_ENABLED=true                # master kill switch; false disables audit hook + returns 503 from Memory API
   ```

3. **Alembic migration** runs through existing CI pipeline — no new tooling

4. **New systemd timers and services**:
   - `/etc/systemd/system/ai-provider-summary.service` — `ExecStart=/opt/ai-provider-service/venv/bin/flask summary-job run --period=day --yesterday`
   - `/etc/systemd/system/ai-provider-summary.timer` — `OnCalendar=*-*-* 02:30:00`, `Persistent=true`
   - `/etc/systemd/system/ai-provider-vault-render.service` — `ExecStart=/opt/ai-provider-service/venv/bin/flask vault-render --check-stale`
   - `/etc/systemd/system/ai-provider-vault-render.timer` — `OnUnitActiveSec=10min`
   - Enable: `sudo systemctl enable --now ai-provider-summary.timer ai-provider-vault-render.timer`

5. **Apache**: no change — Memory API mounts on the same Flask app via existing `ProxyPass`

6. **Backup**:
   - SQLite DB is already in backup — memory data rides along
   - Vault directory is **explicitly excluded** from backup (regenerable from DB)
   - Document in OPERATIONS.md: "Vault is cache, DB is truth"

7. **Rollback**:
   - `MEMORY_ENABLED=false` in env disables the whole feature
   - Dispatcher hook checks flag — `false` skips audit writes
   - Memory API returns 503 when disabled
   - Zero-second recovery on production bug

## Verification per PR

Per AGENTS.md §4 — commit body format:
```
Verified: pytest ✓ (NEW: 142 + 38 memory = 180/180),
manual curl: POST /memory/notes ✓, GET /memory/vault.tar.gz ✓,
NOT deployed to VPS
```

## Documentation updates (AGENTS.md §5.1)

- **AGENTS.md**: add hard rule for `VAULT_PATH` config (§3); extend §6 production reference with vault path and new systemd timers
- **README.md**: setup section gains `VAULT_PATH`; explain SQLite-vs-vault source-of-truth relationship
- **OPERATIONS.md**: backup posture for vault directory; "vault is cache" note

## Delivery strategy

- Branch: `feat/memory-phase1`
- Single PR — schema, MemoryWriter, VaultRenderer, dispatcher hook, Memory API, SummaryJob, systemd units and Alembic migration are tightly coupled; splitting would only create artificial half-states. PR may be reviewed in commit-by-commit fashion (3–8 small commits per AGENTS.md §5).

## Open questions for Phase 2 (out of scope here)

- Prompt injection: which notes get pulled into a chat call, how relevance is scored, token budget enforcement
- Two-way Obsidian sync (rsync-style incremental or proper conflict resolution)
- Custom event-type templates
- Per-user quotas
- Full-text search engine (sqlite FTS5 or external)
- Per-note encryption on disk
