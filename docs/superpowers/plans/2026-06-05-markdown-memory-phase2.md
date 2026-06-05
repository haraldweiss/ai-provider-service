# Markdown Memory Phase 2 — Plan

**Date:** 2026-06-05
**Status:** Draft — open for scoping

> Phase 1 delivered: audit hook, notes/events CRUD, vault render, nightly
> summaries, export API, rate limiting, prompt injection hardening, vault
> host-mount, 167 tests.

---

## Candidates (unordered)

| Theme | Effort | Impact | Notes |
|---|---|---|---|
| **Full-text search** | Medium | High | Replace `LIKE` with FTS5 virtual table; auto-index notes/events on write |
| **Vault sharing** | Medium | Medium | Grant another user read access to a folder; ACL column on `memory_notes` |
| **Scheduled email digests** | Medium | High | Weekly/monthly summary emailed to user; uses existing summary job |
| **WebDAV for Obsidian** | Large | High | Full read/write WebDAV bridge over vault; lets Obsidian open vault directly |
| **Vault backup CLI** | Small | Medium | `flask vault-backup` — tar.gz + DB dump to a configured path |
| **Tags/query enhancement** | Small | Medium | Filter by multiple tags, tag suggestions from existing notes |
| **Audit search UI** | Large | Medium | Web UI to browse/search audit history |

---

## Recommended scope for Phase 2

### 1. Full-text search (FTS5)

Replace the current `LIKE`-based search in `list_notes`/`list_audit` with
SQLite FTS5 for proper tokenization, ranking, and snippet support.

**Files:** `storage/memory_models.py`, `api/memory_api.py`
**New file:** `storage/fts.py` — FTS table management + search helper
**Test:** `tests/test_fts.py`

### 2. Tag query enhancement

Allow `?tags=tag1,tag2` to filter notes matching ALL tags. Add tag
auto-complete endpoint `GET /memory/tags`.

**Files:** `api/memory_api.py`
**Test:** extend `test_memory_api_notes.py`

### 3. Vault backup CLI

`flask vault-backup` — writes `<date>-vault.tar.gz` and `<date>-db.sqlite`
to a configured backup directory.

**Files:** `cli.py`
**Test:** `tests/test_vault_backup_cli.py`

### 4. WebDAV bridge (stretch)

Expose vault as a WebDAV share so Obsidian opens it via `dav://host:port/`.
Use `wsgidav` or a lightweight Flask + `PyWebDAV` middleware.

**Files:** `api/webdav_api.py`, `requirements.txt`
**Test:** `tests/test_webdav.py`

---

## Deferred from Phase 2

- **Scheduled email digests** — needs SMTP config + user preferences table.
  Better as Phase 3 after the user model is richer.
- **Audit search UI** — frontend-heavy. Better as a standalone app or
  WordPress plugin.
- **Vault sharing ACLs** — needs policy design (inherit vs explicit, deny
  priority). Better after multi-user patterns emerge from real usage.

---

## Agent routing

| Task | Agent | Reason |
|---|---|---|
| FTS5 search | opencode | Pure backend + tests |
| Tag enhancements | opencode | Small API extension |
| Vault backup CLI | opencode | New click command + tests |
| WebDAV bridge | **claude-code** | New dependency in requirements.txt, security review of file access |
| Documentation | opencode | OPERATIONS.md update |
