# AGENTS.md — ai-provider-service

Shared instructions for **all AI coding agents** working in this repo (Claude Code, opencode, Cursor, etc.).

---

## 0. Before your first commit in a session

```bash
git config user.email   # must be: harald.weiss@wolfinisoftware.de
git config user.name    # must be: Harald Weiss
git fetch origin        # never work on stale main
```

If `user.email` is unset, empty, or contains `@anthropic` / `@example.com` — **stop, fix, then proceed**.

---

## 1. What this project is

- Centralized AI provider gateway with fallback routing, queue persistence, health monitoring
- Single endpoint for consumer apps (Bewerbungstracker, loganonymizer) to access Claude, Ollama, OpenAI, Mammouth, Custom providers
- Per-user config with Fernet-encrypted API keys, automatic fallback, SQLite-backed queue for offline resilience
- Multi-Mac Ollama pool mode with predictive per-model routing
- Deployed on Rocky 9 VPS behind Apache, reverse-SSH tunnels to local Macs for Ollama
- **Python 3.9+** (Flask, SQLAlchemy, Flask-CORS), SQLite, gunicorn + systemd

---

## 2. Agent routing

### opencode (Throughput-optimized)
- Good for: bulk refactors (type hints, strict mode), dead-code removal, lint cleanup, adding new provider integrations, test coverage
- Avoid: production deploys, DB migrations, VPS config changes

### Claude Code (Care-optimized)
- Good for: production deploys (`systemctl restart ai-provider-service`), DB schema migrations, reverse-SSH tunnel changes, Apache/SELinux config, security review of new endpoints or file ops

---

## 3. Hard rules

### 3.1 API keys are Fernet-encrypted at rest
- Never log or expose decrypted keys, even in debug output
- `fernet_key` is set via env var `FERNET_KEY` — never hardcode

### 3.2 SQLite path is set by the systemd unit
- `/etc/ai-provider-service/provider.db` on VPS
- Never reference a hardcoded path — use config from env or `app.config`

### 3.3 HTTP calls to Ollama Macs go through reverse-SSH tunnels
- Ports 11434 (Macbook) and 11435 (Mac mini) are tunneled from VPS localhost
- Never assume Ollama is available locally — always handle `ConnectionError` with fallback to next provider

### 3.4 Provider health checks are async and non-blocking
- ✅ Use thread pool or async for parallel health checks
- ❌ Serial `for provider in providers: health_check(provider)` — blocks the gateway

### 3.5 Gunicorn behind Apache
- Service runs via `systemd` → `gunicorn` on a local socket or port
- Apache reverse-proxies with `ProxyPass`
- Never bind directly to port 80/443

---

## 4. Verification standards

Record in commit body. Examples:

```
Add: Mammouth provider integration

Verified: pytest ✓ (142/142), php -l N/A, manual curl test against
gateway ✓ (Mammouth + fallback to Ollama), NOT deployed to VPS
```

```
Refactor: extract provider base class

Verified: pytest ✓ (142/142), NOT manually tested against live providers
```

---

## 5. Commit style

- Prefix required: `Add` / `Fix` / `Update` / `Refactor` / `Doc` / `Test` / `Perf` / `Security`
- Granular: 3–8 small commits per topic
- Bug reproducer in body when applicable

---

## 6. Production access reference

| What | How |
|---|---|
| SSH | `ssh ionos-vps` |
| App dir | `/opt/ai-provider-service/` |
| Logs | `/var/log/ai-provider-service/` |
| Service | `sudo systemctl status ai-provider-service` |
| Restart | `sudo systemctl restart ai-provider-service` |
| DB | SQLite at `/etc/ai-provider-service/provider.db` |
| Tunnels | `autossh` systemd units (check `systemctl list-units \| grep tunnel`) |

---

## 7. Handoff zone

### 2026-05-30 — Provider access control + opencode.ai integration (in progress, handoff to opencode)

**Spec:** [`docs/superpowers/specs/2026-05-30-provider-access-control-design.md`](docs/superpowers/specs/2026-05-30-provider-access-control-design.md)
**Plan:** [`docs/superpowers/plans/2026-05-30-provider-access-control.md`](docs/superpowers/plans/2026-05-30-provider-access-control.md)
**Branch:** `feat/provider-access-control` (already checked out; not yet pushed)
**Worked by:** Claude Code (subagent-driven execution). Stopping after Task 4 implementation to hand off the remaining bulk implementation to opencode (good throughput-fit per §2).

**Done (commits on branch):**
- Task 1 — `ProviderGrant` model (`0579dd3`) + cleanup (`00b7ab5` — SPDX header, tighten `IntegrityError` catch)
- Task 2 — Config additions for admin/gate/opencode/session (`04d4056`) + cleanup (`cc7c62f` — hoist test-isolation `import api.auth` shim into `tests/conftest.py`)
- Task 3 — `Principal` + `auth.py` rewrite (`d962596`) + hardening (`b20aa09` — `hmac.compare_digest`, `_attach()` helper, pin empty-user_id test)
- Task 4 — Gate module (`924c9e1`) — **implementer DONE, two reviews completed, cleanup NOT yet applied; see below**

**Remaining (Tasks 4 cleanup, 5–15):**

**Task 4 open cleanup** (code-review findings on `924c9e1`, fix BEFORE Task 5):
1. **Critical — Python 3.9 compat.** `api/gate.py:35` uses `str | None` (PEP 604). Add `from __future__ import annotations` at top of `api/gate.py` (matches `api/usage_api.py`, `providers/ollama.py`). Without this, gate fails to import on the 3.9 baseline stated in CLAUDE.md (VPS runs 3.12 so prod works, but baseline is broken).
2. **Important — Guard missing `g.principal`.** `require_provider_access` should return 401 `{'error': 'unauthenticated'}` if `getattr(g, 'principal', None)` is falsy, rather than letting AttributeError surface as 500.
3. **Important — Use `arg_name` in the 400 message.** Currently hardcoded `'missing provider_id'`; change to `f'missing {arg_name}'`.
4. **Important — Test the `body.get('provider')` fallback.** The decorator accepts `body['provider']` as a fallback for `/chat`'s JSON shape, but no test covers it. Add `test_decorator_extracts_provider_from_chat_body` to `tests/test_gate.py` (template in the plan / the canceled cleanup spec — POST `/chat`-shape with `{user_id, provider}` body, assert 200 for ollama and 403 for claude).

After applying: `pytest -q` should be 51/51 (was 50 + 1 new). Commit as `Fix:` with a `Verified:` line.

**Tasks 5–15** — follow `docs/superpowers/plans/2026-05-30-provider-access-control.md` verbatim. Each task in the plan is self-contained (file paths, complete code, TDD steps, exact commit message). No additional context from this session is needed.

**Conventions discovered the hard way (so opencode doesn't re-trip on them):**
- Test files need SPDX header: `# SPDX-License-Identifier: AGPL-3.0-or-later` on line 1.
- `tests/conftest.py` already eager-imports `api.auth` to keep `Config` bindings stable across `importlib.reload(config)` in tests. Don't remove that line.
- `Principal(user_id='', role='user')` is intentional for SERVICE_TOKEN calls without an asserted `user_id` — pinned by `test_service_token_with_no_user_id_yields_empty_principal`. Route-level validation (`/chat`) and gate denial cover real call sites; don't reject at auth boundary.
- Commit prefixes used: `Add: / Fix: / Update: / Refactor: / Doc: / Test: / Perf: / Security:`. Body must include a `Verified:` line listing what was tested.
- Recent commits suggest using SQLAlchemy 2.x style where possible (`db.session.get(...)`, not `Query.get`).
- Token comparison must be `hmac.compare_digest` (see `b20aa09`); not `==`.
- For union types in production code (Tasks 5–15): use `from __future__ import annotations` OR `Optional[X]` — never bare `X | None` without the future import.

**Verification baseline at handoff:** `pytest -q` → 50 passed (was 26 at branch start; +24 from Tasks 1–4). Keep this green per task.

**Then:** Tasks 14 and 15 of the plan (deploy steps, VPS smoke test) should be handed BACK to Claude Code for the VPS-touching parts — opencode shouldn't deploy. Code-only parts of Task 14 (README.md / OPERATIONS.md / .env.example edits) and Task 15 (E2E pytest) are fine for opencode.

**Open carry-overs from spec §14:**
- `pricing.py` opencode entries: leave the dict empty if rate-card numbers aren't handy at implementation time (Task 7).
- opencode.ai exact auth format: assumed Bearer + OpenAI-compatible. Verify against current opencode.ai docs in Task 6; patch `OpencodeClient.__init__` if needed.
