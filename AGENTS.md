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

### Provider access control + opencode.ai integration

**Status:** Implementation complete, deployed to VPS (2026-05-30) per
[`docs/superpowers/plans/2026-05-30-provider-access-control.md`](docs/superpowers/plans/2026-05-30-provider-access-control.md)
([spec](docs/superpowers/specs/2026-05-30-provider-access-control-design.md)).

**Deployed:** Yes — VPS at `bewerbungen.wolfinisoftware.de` (see OPERATIONS.md).
All 89 tests pass (`pytest -q`).

**Admin UI URL:**
`https://bewerbungen.wolfinisoftware.de/ai-provider/admin/ui/?token=<ADMIN_TOKEN>`
(token in VPS `.env`). Also linked from WordPress Admin Dashboard at
`/wp-admin/tools.php?page=wolfini-admin-tools`.

**What was deployed (branch `feat/provider-access-control`):**
- Provider-access gate (`GATE_ENABLED=true`) with `flask grants-bootstrap` run
- `ADMIN_TOKEN`, `SECRET_KEY` set in VPS `.env`
- `openai` package installed on VPS (opencode provider dependency)
- Apache config updated: `X-Forwarded-Proto` and `X-Forwarded-Prefix` headers
- `ProxyFix` middleware added to Flask for reverse-proxy URL generation
- Admin UI: session auth, users overview, detail page, approve/revoke buttons
- User profiles: alias, add/remove users (soft-delete via `disabled` flag)
- Bugs fixed during deployment: trailing-slash redirects, JS event handler
  issues (`stopPropagation`/`data-mode` timing), PATCH/DELETE creating
  `UserProfile` rows on first use, `build_overview` restoring after accidental
  deletion, `UserProfile` union query for discovered users

**Pricing entries for opencode.ai:** populate `pricing.py` with the current
Zen rate card before relying on cost tracking.

**Root cause index (bugs encountered & fixed):**

| Symptom | Root cause | Fix |
|---|---|---|
| Admin UI redirects to wrong URL behind Apache | `redirect(request.path)` returns path w/o `/ai-provider/` prefix | ProxyFix + `url_for(request.endpoint)` in `_entry` handler |
| Edit alias → "save" triggers immediately | `data-mode=save` set synchronously during edit click event | `setTimeout(0)` to defer attribute |
| Edit alias → "error" on discovered users | PATCH returns 404 for users without `UserProfile` row | Auto-create `UserProfile` on PATCH |
| Remove user → "error" on discovered users | DELETE returns 404 for users without `UserProfile` row | Auto-create `UserProfile` on DELETE |
| Add user → not shown in overview | `build_overview()` only queried configs/grants/usage | Added `UserProfile` to union query |
| JS edit/save button double-fires | `stopPropagation()` in edit handler blocked save handler | `cloneNode(true)` then direct `addEventListener` (eventual fix: `data-mode` flag) |
| Approve/revoke → state not refreshed | No `location.reload()` after success | Added `location.reload()` in both overview and detail page |
| `build_overview` missing (NameError) | Accidentally deleted during user profile endpoint edit | Restored function |
