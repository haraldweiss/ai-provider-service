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

### 3.6 Markdown memory vault is rendered, not authored
- `VAULT_PATH` (default `/var/lib/ai-provider-service/vault`) contains `.md` files **generated from the DB** by `VaultRenderer`. Treat as cache.
- DB tables `memory_notes` and `summary_jobs` are the source of truth.
- ✅ Edit notes via `PATCH /memory/notes/<id>`.
- ❌ Hand-edit `.md` files under `VAULT_PATH` — the next self-heal cron will overwrite them.
- ❌ Reference a hardcoded vault path; read `Config.VAULT_PATH` (mirrors §3.2 SQLite rule).

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

## 5.1 Sync discipline — git, AGENTS.md, README must stay current

Cross-project rule (canonical statement in `wolfini_de_web` AGENTS.md §5.1). Every non-trivial change in this repo must update three artifacts in lockstep:

1. **Git** — commit the change. Don't end a session with uncommitted operational work in the tree. If a session can't commit (blocked hook, etc.), say so in the handoff entry (§7).
2. **AGENTS.md** — update whenever the change adds/modifies/invalidates a hard rule (§3), a deploy/verify procedure (§4-§6), or a follow-up the next session needs (§7). Includes *removing* stale entries in the same commit they go obsolete.
3. **README** — update when the change affects setup, env vars, ports, the Quadlet, ownership/permission expectations, deploy steps, or known caveats. Create one if missing AND the change warrants it.

If a sibling repo is touched in the same session (`wolfini_de_web`, `Claude-KI-Usage-Tracker`, `Bewerbungstracker`), the same three artifacts must be updated *there too* — link the sibling PR from the handoff entry.

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
| Vault path | `/var/lib/ai-provider-service/vault/<user>/...` (cache; regen via `flask vault-render --rebuild`) |
| Timers | `systemctl list-timers \| grep ai-provider` — summary @ 02:30 UTC, vault-render every 10 min |

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
`/wp-admin/tools.php?page=wolfini-admin-tools` (plugin `wolfini-admin-tools`
in `wp-content/plugins/wolfini-admin-tools/`, activated via WP-CLI).

**Note:** The file was originally in the theme dir but never loaded — WordPress
only loads plugins from `wp-content/plugins/`. Moved and activated via
`wp plugin activate wolfini-admin-tools --path=/var/www/wolfinisoftware`.

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

**Pricing entries for opencode.ai:** populated via `flask update-opencode-pricing`
CLI command which fetches the current Zen rate card from opencode.ai/docs/zen/
and writes `pricing_overrides.json`. Merged with static `pricing.py` dict at
runtime. A daily cron (06:00 UTC) keeps pricing up to date.
See `pricing.py:_load_merged_pricing()` and `cli.py:fetch_opencode_pricing()`.

**Claudetracker integration:** A `POST /api/local-usage/discover` endpoint
was added to the claudetracker backend (repo `Claude-KI-Usage-Tracker`,
commit `69c6403`). It calls the ai-provider-service `/admin/overview` API
and imports all discovered users into the sync list. The frontend
ProviderServiceSettings component has a green "User importieren" button.
Available at `https://claudetracker.wolfinisoftware.de/` → Settings →
AI-Provider-Service.

### Serena MCP — setup verification

**Status:** Already installed & configured (2026-05-30, via opencode).

Serena 1.5.3 (`uv tool install -p 3.13 serena-agent`) ist global installiert und
initialisiert (LSP-Backend, `~/.serena/serena_config.yml`). Der MCP-Server ist
in `~/.config/opencode/opencode.jsonc` aktiviert:

```json
{
  "mcp": {
    "serena": {
      "type": "local",
      "command": ["serena", "start-mcp-server", "--context=opencode", "--project-from-cwd"],
      "enabled": true
    }
  }
}
```

`--project-from-cwd` erkennt das Projekt automatisch — kein per-project Setup nötig.
Nächstes opencode hier startet Serena automatisch mit.

---

 ### Markdown memory — Phase 1 + Phase 2

**Status:** All implemented, merged to `main` (2026-06-05), deployed to VPS.
183 tests passing (`pytest -q`).

**VPS deployment:** Container `localhost/ai-provider:latest` managed by
`ai-provider.service` (systemd, rootful podman, `--security-opt label=disable`).
DB at `/opt/ai-provider-data/storage.db`. Vault host-mounted at
`/var/lib/ai-provider-service/vault/`.

**Phase 1 — Core:**
- MemoryNote + SummaryJob ORM models, MemoryWriter, VaultRenderer
- Dispatcher audit hook (gated by `MEMORY_ENABLED`)
- `/memory/notes` CRUD, `/memory/events`, `/memory/audit`, `/memory/summaries`,
  `/memory/notes/<id>/summarize`
- `/memory/vault.tar.gz` + `/memory/vault/<path>` with path-traversal guard
- `flask summary-job` + `flask vault-render` + `flask vault-backup` CLI commands
- systemd timer units for summary (@02:30 UTC) + vault self-heal (10 min)

**Phase 1.5 (deferred → delivered in same session):**
- Rate limiting: in-memory sliding window (60 POST/min, 120 GET/min, 5 vault exports/min)
- Prompt injection sanitizer: strips control chars, escapes `{{`/}}`/```` ``` ````
- `vault.tar.gz` hardening: symlink filter, resolved-path containment, 256 MiB cap
- Vault host-mount: systemd unit mounts `/var/lib/ai-provider-service/vault`

**Phase 2:**
- FTS5 full-text search (porter+unicode61, auto-synced via triggers)
- Tag filter (`?tags=a,b`) + `GET /memory/tags` endpoint
- WebDAV bridge (pure Flask + ElementTree) — Obsidian opens vault directly at
  `https://host/ai-provider/memory/dav/?user_id=<id>`

**Key VPS quirks encountered:**
- Podman 5 changed bridge IP from `10.88.0.1` → `10.89.0.1` — Quadlet broke
- SELinux MCS mismatch between volume `:Z` and container process label —
  workaround: `--security-opt label=disable`
- Rootless user service under `poduser` kept restarting the old `main` container —
  disabled via `systemctl --user disable ai-provider.service`
- `fuser` is at `/usr/sbin/fuser` on Rocky 9, not `/usr/bin/fuser`

**Caveat for testing:** `test_memory_config` uses `importlib.reload(config)` which
creates a new Config class. Tests that monkeypatch Config must import the module
(`import config as m; monkeypatch.setattr(m.Config, ...)`) rather than patching
the locally-imported `Config` name. See `test_dispatcher_audit_hook.py:memory_enabled`
fixture for the pattern.

**Rollback:** set `MEMORY_ENABLED=false` in `/etc/ai-provider/ai-provider.env`
and `systemctl restart ai-provider.service`.

**Sibling-Repos haben Memory-Doku-Sync** (2026-06-06, per §5.1):
- Bewerbungstracker `master` commit [`728460f`](https://github.com/haraldweiss/Bewerbungstracker/commit/728460f) — §7 Eintrag mit Use-Case-Ideen (event_type=application_created)
- Claude-KI-Usage-Tracker `main` commit [`58704d5`](https://github.com/haraldweiss/Claude-KI-Usage-Tracker/commit/58704d5) — §7 Eintrag mit Use-Case-Ideen (workspace_discovered events, cost-alert notes)
- Beide schreiben aktuell NICHT in Memory; die Doku ist informativ damit kommende Integrations-Sessions wissen dass das verfügbar ist.

---

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
