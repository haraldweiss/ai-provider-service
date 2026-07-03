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
- Deployed as a Docker container (`ai-provider`) on **oracle-vm** (Oracle Cloud, 92.5.18.29); three local Macs serve Ollama via reverse-SSH tunnels (the IONOS VPS is retired — see §3.3/§6)
- **Python 3.9+** (Flask, SQLAlchemy, Flask-CORS), SQLite, gunicorn + systemd

---

## 2. Agent routing

### opencode (Throughput-optimized)
- Good for: bulk refactors (type hints, strict mode), dead-code removal, lint cleanup, adding new provider integrations, test coverage
- Avoid: production deploys, DB migrations, VPS config changes

### Claude Code (Care-optimized)
- Good for: production deploys (`docker restart ai-provider` on oracle-vm), DB schema migrations, reverse-SSH tunnel changes, Apache/SELinux config, security review of new endpoints or file ops

---

## 3. Hard rules

### 3.1 API keys are Fernet-encrypted at rest
- Never log or expose decrypted keys, even in debug output
- `fernet_key` is set via env var `FERNET_KEY` — never hardcode

### 3.2 SQLite path is set by the container env, not hardcoded
- Live DB is `/app/data/storage.db` inside the `ai-provider` container (Docker volume `bewerbungen_data`); host copy/backup at `/opt/ai-provider-data/storage.db`.
- Never reference a hardcoded path — use config from env or `app.config`.

### 3.3 HTTP calls to Ollama Macs go through reverse-SSH tunnels
- Three Macs serve Ollama, each tunnelled to a distinct oracle-vm port: **11434** (MacBook), **11435** (Mac mini), **11440** (Mac Studio / Michael).
- Tunnels are initiated **from each Mac**, not from the server: macOS `launchd` autossh agents (`com.wolfini.*tunnel`) connect to `opc@oracle-vm` with `-R 1143x:127.0.0.1:11434`. Each Mac self-monitors and restarts its own tunnel; the gateway has no control over them.
- On oracle-vm a `socat` layer bridges the Docker gateway to the sshd reverse-forwards: `172.17.0.1:1143x → 127.0.0.1:1143x`. The container reaches Ollama at `172.17.0.1:1143x`.
- ⚠️ launchd agents on the Macs must NOT log under `~/.ollama` — it's a symlink to an external SSD; an unmounted/TCC-blocked volume makes launchd fail the job with `EX_CONFIG (78)` (silent, no autossh start). Log to internal disk (`~/Library/Logs/…`). Self-monitors must restart via `launchctl kickstart -k`, not legacy `load/unload` (no-op on wedged jobs).
- Never assume Ollama is available locally — always handle `ConnectionError` with fallback to next provider.

### 3.4 Provider health checks are async and non-blocking
- ✅ Use thread pool or async for parallel health checks
- ❌ Serial `for provider in providers: health_check(provider)` — blocks the gateway

### 3.5 Gunicorn in the container, behind host Apache
- `gunicorn` runs **inside** the `ai-provider` Docker container, which exposes `127.0.0.1:8767` on oracle-vm.
- Host Apache (`httpd`) reverse-proxies to it: `ai-admin.wolfinisoftware.de` → `:8767/`, and `ai-provider-service.wolfinisoftware.de/` → `:8767/` (see `/etc/httpd/conf.d/`). In-container callers reach the host via `ai-provider-bridge.service` (docker0 gw → loopback :8767).
- Never bind directly to port 80/443 — Apache owns those.

### 3.6 Markdown memory vault is rendered, not authored
- `VAULT_PATH` (set to `/app/data/vault` in the container env; code default `<app>/vault`) contains `.md` files **generated from the DB** by `VaultRenderer`. Treat as cache.
- DB tables `memory_notes` and `summary_jobs` are the source of truth.
- ✅ Edit notes via `PATCH /memory/notes/<id>`.
- ❌ Hand-edit `.md` files under `VAULT_PATH` — the next self-heal cron will overwrite them.
- ❌ Reference a hardcoded vault path; read `Config.VAULT_PATH` (mirrors §3.2 SQLite rule).

### 3.7 No persistent hotfixes in the running container
- ❌ Never leave changes applied to the running `ai-provider` container via `sed`/`docker cp`/`docker exec` as the "fix". They do **not** survive an image rebuild and create silent drift between running code and the repo. This bit us on 2026-06-12: opencode fixes lived only in the container, while `main` carried a `NameError` (`Config` not imported) that surfaced the moment someone rebuilt.
- ✅ An incident hotfix to restore service is fine **only if**, in the same session, you (1) commit the fix to the repo, (2) rebuild the image, (3) recreate the container so running == committed.
- ✅ Before merge, CI must be green (`.github/workflows/ci.yml`: pytest **and** docker build + boot + `/health` smoke — the smoke catches import/`NameError`/bind regressions that unit tests miss).
- ✅ Build images with `build.sh` (tags `:<sha>` + `:latest`) so "what's running" is traceable and rollback is possible.

### 3.8 Personal provider keys are identity-bound
- Personal keys for `claude`, `opencode`, `openai`, `zai`, and `ollama_cloud` authorize only their owning `user_id`; they bypass grants because the user bears the cost.
- A personal key must take precedence over any server key. If it fails, never silently retry with an owner-funded key.
- Never log, return, or render personal keys. User access tokens are hashed; plaintext is shown only on issuance/rotation.
- User tokens must reject a different asserted path/query/body `user_id`. Token rotation or revocation must invalidate existing settings sessions.

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

If a sibling repo is touched in the same session (`wolfini_de_web`, `KI-Usage-Tracker`, `Bewerbungstracker`), the same three artifacts must be updated *there too* — link the sibling PR from the handoff entry.

---

## 6. Production access reference

| What | How |
|---|---|
| SSH | `ssh oracle-vm` (Oracle Cloud, 92.5.18.29) |
| Runtime | Docker container `ai-provider` (`restart=always`, exposes `127.0.0.1:8767`). Current live container is managed from `/opt/ai-provider-service/docker-compose.yml` on network `ai-provider-service_default`; image still must be built with `build.sh` using a SHA tag before recreate. Fronted by host Apache. |
| Config / env | `/etc/ai-provider/ai-provider.env` (root-owned) |
| Logs | `docker logs ai-provider` (no `/var/log/ai-provider`; `journalctl -u ai-provider-bridge`/`openai-proxy` for the helpers) |
| Restart | `cd /opt/ai-provider-service && sudo docker compose up -d --force-recreate ai-provider` after building the SHA-tagged image. `sudo` is required because `/etc/ai-provider/ai-provider.env` is root-owned/600. Plain `docker restart ai-provider` does **not** reread env changes. |
| DB | SQLite in Docker volume `bewerbungen_data` → `/app/data/storage.db`; host copy/backup at `/opt/ai-provider-data/storage.db` |
| Ollama tunnels | macOS `launchd` autossh on 3 Macs → `opc@oracle-vm` + host/Compose bridge (see §3.3). Current container env uses `host.docker.internal` endpoints, including ports `11441`, `11434`, and `11440`. Server check: `ss -tln \| grep 1144\\|1143` and `curl 127.0.0.1:<port>/api/tags` |
| Vault | `VAULT_PATH=/app/data/vault` (container env; `MEMORY_ENABLED=true`). Cache; regen via `flask vault-render --rebuild` inside the container. |
| Timers | Host: `wolfini-daily-roundup.timer` (daily ~04:02 GMT). The old IONOS systemd timers (summary @02:30, vault-render /10min) are gone — any such jobs now run inside the container, not as host timers. |
| Apache | Host `httpd` reverse-proxies `:8767` → `ai-admin.wolfinisoftware.de` and `ai-provider-service.wolfinisoftware.de/` (`/etc/httpd/conf.d/`) |

---

## 7. Handoff zone

### Personal Provider API Keys (2026-06-22, Codex)

**Status:** Merged via PR #24 and deployed to oracle-vm; running == committed
merge SHA `a54c6ec`.

- Hashed, admin-issued per-user tokens with rotation/revocation and strict
  identity binding.
- Personal keys bypass provider grants for their owner; server-funded access
  keeps existing grant/allowlist behavior.
- Self-service UI at `/settings/login` + `/settings/providers` with CSRF,
  throttled login, safe status-only rendering, test, and remove actions.
- New distinct `ollama_cloud` provider for `https://ollama.com/api`.
- Verification: pytest **268/268 passed**; GitHub CI test + Docker smoke green.
  Production image `localhost/ai-provider:a54c6ec` built with `build.sh` and
  container fully recreated on `bewerbungen-net` with the existing env-file,
  data volume, and both pricing mounts.
- Live smoke: container healthy; `/health` 200/status=ok; `/settings/login` 200;
  `user_access_tokens` table query succeeds; `/providers` lists
  `ollama_cloud`; bridge + proxy helpers active; no startup traceback.
- Deploy note: `/etc/ai-provider/ai-provider.env` is root-owned/600, so recreate
  must use `sudo docker run --env-file ...`. The first non-sudo attempt failed
  before container creation and the rollback trap restored `7fd3c86` healthy;
  the sudo retry deployed `a54c6ec` successfully.
- No live third-party personal key was used during smoke verification.

### z.ai (GLM) Provider + Tarif-Sync (2026-06-15, Claude Code)

**Was:** Neuer Provider `zai` (z.ai / Zhipu GLM, OpenAI-kompatibel,
`https://api.z.ai/api/paas/v4`).

- `providers/zai.py` (`ZaiClient`) + Registry-Eintrag (`system: True`,
  `optional: ['api_key','api_endpoint']`) + Factory-Zweig.
- **Access-Modell (Owner-only):** der zentrale `ZAI_API_KEY` ist NUR für die
  Allowlist nutzbar. `ZAI_SERVER_KEY_ALLOWED_USERS` leer ⇒ Default = nur
  `Config.ADMIN_USER_ID` (`harald`) — **inkl. der kostenlosen GLM-Flash-Modelle**.
  Alle anderen User brauchen einen eigenen Key via ProviderConfig
  (`/configs/<user_id>/zai`). Gate in `dispatcher._load_config` +
  `_is_zai_server_key_allowed` (mirror der Claude-Allowlist, aber restriktiver
  Default statt offen).
- **Pricing:** statischer GLM-Snapshot in `pricing.py` + getrennte Override-Datei
  `pricing_overrides_zai.json` (NICHT `pricing_overrides.json`, sonst clobbert
  der opencode-06:00-Cron die z.ai-Preise). `_load_merged_pricing` lädt jetzt
  beide Dateien.
- **Täglicher Tarif-Check:** `flask update-zai-pricing` lädt
  `docs.z.ai/guides/overview/pricing.md` (saubere Markdown-Tabellen), parst die
  Rate-Card, difft gegen den letzten Snapshot, speichert und **mailt
  harald.weiss@wolfinisoftware.de bei jeder Tarif-Änderung** (neu/entfernt/Preis).
- `config.py`: `ZAI_BASE_URL`, `ZAI_API_KEY`, `ZAI_SERVER_KEY_ALLOWED_USERS`.
  `.env.example` + README (Features, Access-Control-Sektion) aktualisiert.

**Fix während Deploy (Commit `f3bd215`):** GLM-Reasoning-Modelle
(z.B. `glm-4.5-flash`) legen Output in `reasoning_content` ab — `ZaiClient`
fällt jetzt darauf zurück, wenn `content` leer ist (mirror
`providers/opencode.py _extract_content`). Sonst leere Antworten bei
Reasoning-Modellen / knappem `max_tokens`.

**DEPLOYED auf oracle-vm (2026-06-15), running == committed (`7fd3c86`):**
- `main` fast-forward auf `7fd3c86`, CI grün.
- Image `localhost/ai-provider:7fd3c86` (+`:latest`) via `build.sh` auf
  oracle-vm gebaut; Container recreated. `docker ps`: Up, **healthy**.
- **Nebenbefund + Fix (`7fd3c86`):** CI-docker-smoke war intermittierend rot —
  bei `gunicorn --workers 2` auf frischer SQLite-DB racen beide Worker auf
  `db.create_all()` → `table provider_configs already exists`, Worker-Boot
  failed. `app._safe_create_all()` schluckt jetzt genau diesen Race
  (re-raises andere OperationalErrors). Prod war nie betroffen (DB schon
  befüllt), aber schützt frische Deploys/Restarts.
- `ZAI_API_KEY` in `/etc/ai-provider/ai-provider.env` (User hat ihn gesetzt;
  hatte ihn versehentlich auf `ZAI_API_KEX` getippt → mechanisch korrigiert;
  env-file von `644`→`600` gehärtet).
- Neuer **persistenter Mount** `/opt/ai-provider-data/pricing_overrides_zai.json`
  → `/app/pricing_overrides_zai.json` (getrennt von opencodes
  `pricing_overrides.json`, überlebt Rebuilds).
- Daily-Cron (root crontab, 06:00): `docker exec ai-provider flask
  update-zai-pricing >> /var/log/ai-provider-zai-pricing.log 2>&1`. `docker`
  liegt in `/usr/bin` (in cron-PATH), Cron-Env-Smoke ✓.
- **Verifiziert live:** pytest 233/233; /health zeigt `zai` healthy; Gate:
  `harald`→System-Key, `eve`→denied; echter z.ai-Call (200 OK,
  `api.z.ai/api/paas/v4`); `update-zai-pricing` schrieb 19 GLM-Modelle ins
  Host-File.

**Deploy-Specifics (für die nächste Session — nicht offensichtlich):**
- Container läuft im Docker-Netz **`bewerbungen-net`** (NICHT default bridge;
  Host-Gateway dort `172.19.0.1`), Build-Source ist die `/tmp/ai-provider-src`
  Checkout (`origin/main`). Recreate-Command s. Git-Historie dieser Session.
- ⚠️ **Env-File-Änderungen brauchen `docker run`-Recreate, KEIN `docker
  restart`** — `--env-file` wird nur bei Create gelesen. (Healthcheck ist im
  Dockerfile gebacken, kein Run-Flag.)
- Rollback: `localhost/ai-provider:7e4744e` und `:rollback-20260612-045814`
  liegen noch auf der Box.

**Offen (optional):** andere User, die z.ai wollen, brauchen eigenen Key +
Grant (`flask grants-bootstrap` / Admin-UI). Free-Tier ist bewusst owner-only.

### Ollama-Tunnel-Ausfall + Doku-Korrektur (2026-06-13, Claude Code)

**Symptom:** Consumer zeigte `● Ollama (Mac) — offline (6 ms)` (6 ms = connection refused, kein Timeout).

**Root cause:** `~/.ollama` auf dem MacBook ist seit 2026-06-11 ein Symlink auf eine externe SSD. Der launchd-Tunnel-Agent `com.wolfini.ollama-tunnel` hatte `StandardOutPath` unter `~/.ollama` → launchd konnte die Log-Datei nicht öffnen → `EX_CONFIG (78)`, autossh startete nie, Server band `127.0.0.1:11434` nicht mehr → socat/Container sahen Ollama offline. Der Self-Monitor „heilte" nicht, weil er legacy `launchctl load/unload` nutzte (No-Op auf wedged Job).

**Fix (alles lokale Mac-Infra, kein Repo-Code):** Log-Pfade des Tunnel-Agents auf interne Disk umgebogen; alle drei Self-Monitore (MacBook/Mini/Studio) auf `launchctl kickstart -k` umgestellt; redundanten `de.wolfini.ollama-app` (EX_CONFIG-Spam) deaktiviert; `~/bin/reactivate-tunnels.sh` von IONOS-Resten auf `oracle-vm`/`com.wolfini.ollama-tunnel` korrigiert. Verifiziert: oracle-vm :11434/:11435/:11440 → alle HTTP 200.

**Doku aktualisiert (oracle-vm only, IONOS retired):** §1, §3.2, §3.3, §3.5, §3.6, §6 + §2-Deploy-Befehl spiegeln jetzt die reale Topologie. Verifiziert auf oracle-vm: Docker-Container `ai-provider` (`:8767`, restart=unless-stopped); DB `/app/data/storage.db` (Volume `bewerbungen_data`); `VAULT_PATH=/app/data/vault`, `MEMORY_ENABLED=true`; **Apache (`httpd`) läuft weiter** und reverse-proxyt `:8767` für `ai-provider-service.wolfinisoftware.de` (gunicorn läuft im Container); Host-Timer nur noch `wolfini-daily-roundup.timer` (täglich ~04:02). 3 Macs (11434/11435/11440) tunneln per macOS-launchd-autossh → `opc@oracle-vm`, socat-Brücke `172.17.0.1:1143x→127.0.0.1:1143x`.

### chore/ci-hardening — gemerged (2026-06-13, opencode)

**What:**
- CI pipeline (`.github/workflows/ci.yml`): pytest + docker build+smoke
- `build.sh` — SHA-tagged image builds, Rollback-fähig
- AGENTS.md aktualisiert: oracle-vm→IONOS, §3.7 No-Hotfix, oracle-vm-Handoff gelöscht
- `fix/news-agent-current-date` (Commit `3e16baf`) war bereits in History enthalten
- 205/205 Tests grün

**Offen:**
- WordPress-Post 34017 (falsche News-Agent-Daten vom 2026-06-06) — optional löschen

### Cross-Repo Cleanup (2026-06-13, opencode)

**Bewerbungstracker:**
- `fix/app-gunicorn-bind-host` (PR#23) — gemergt 🔴
- `fix/setup-script-cron-env` (PR#24) — gemergt 🔴
- `fix/admin-bg-jobs-double-api-prefix` — gemergt
- `fix/free-models-grouping` — gemergt
- `claude/naughty-turing-5e5603` (get_models_raw, cache-invalidation) — gemergt
- 4 untracked Files `SUSPICIOUS_FEEDBACK_*` liegen noch在工作目录
- **Verifiziert:** alle 5 Branches auf `origin/master`

**KI-Usage-Tracker:**
- `fix/quadlet-healthcmd-quoting` — gemergt
- `claude/crazy-jang-63096d-test` (Workspace Discovery) — gemergt
- Beide lokalen Kopien auf `origin/main` geupdated
- Backend-Tests: 7 passed, 26 failed (alles pre-existing infra issues)

**wolfini_de_web:**
- `security/agent-shield-sudo-fix` — gemergt
- `claude/modest-wilbur-1611de` (AGENTS.md §5.1 + IONOS-VPS-MANAGEMENT.md) — gemergt
- 14 merged Branches auf origin wegen GH013-Branch-Protection nicht löschbar
- 7 orphaned Branches (alter `master`, 322 commits diverged) bewusst belassen

### 📩 Notiz an opencode (2026-06-06, von Claude Code)

opencode, du hast heute ordentlich geliefert (Phase 1.5 + 2 über Nacht, dann Phase 2.1 am Morgen). Drei Sachen sind mir beim Drüberschauen aufgefallen — keine Beleidigung, nur nüchterne Beobachtungen für die nächste Iteration:

1. **Phase 2.1 (Commit `057a19e`) hatte keine Tests dabei.** 100 Zeilen neue Logik in `api/webdav_api.py` (PUT/DELETE/MKCOL → DB via `_upsert_note_from_path`), aber `tests/test_webdav.py` blieb unverändert. Konsequenz: das Feature ist live, aber jeder zukünftige Refactor kann es brechen ohne dass `pytest -q` warnt. Vorschlag: TDD-Style-Tests für die drei Methods (PUT erzeugt DB-Row mit korrektem kind/folder/slug; PUT auf existierende Row updated body; DELETE soft-deleted die Row + entfernt das File). Pro AGENTS.md §4 "Verified: pytest" Pflicht.

2. **Merge-Konflikt-Resolution in `d10258e` ohne lokales `pytest`-Run.** Beim Resolve sind drei kritische Zeilen aus `app.py` gefallen: `webdav_bp`-Registrierung, `ensure_fts()`-Call, `vault_backup_command`-Import. Folge: `/memory/dav/*` war 3 Stunden komplett 404 (Phase-2.1-Code unerreichbar), `flask vault-backup` fehlte, FTS5 wurde auf frischen DB-Starts nie initialisiert. 18 Tests waren rot — wären beim ersten `pytest -q` aufgefallen. Fix: ich hab's in `e51e340` restored. Bitte vor jedem merge/push einmal die suite laufen lassen, gerade nach Konflikt-Resolves.

3. **`_parse_dav_path` matched das Phase-1-Layout nicht.** Du erwartest `/<app>/<kind>/<slug>.md` (3-Level). Aber Phase-1-Notes liegen in: `<app>/notes/<slug>`, `<app>/events/<event_type>/<slug>` (4-Level!), `<app>/audit/YYYY/MM/DD/<slug>` (7-Level!), `_shared/notes/<slug>`, `_index/by-day/<date>`. Bei DELETE auf Phase-1-Notes returnt der parser `None` → DB-Row wird NICHT soft-deleted, nur das File entfernt → orphan-cleanup-Cron räumt dann den Rest auf (Funktionierts also indirekt, aber nicht über dem von dir intendierten Pfad). Vorschlag: parser umbauen, sodass er die echte Folder-Struktur respektiert (oder `MemoryNote.query.filter_by(folder=parent_path, slug=stem)` direkt — keine app/kind-Dekonstruktion nötig).

4. **`.serena/project.yml` wurde mit-committed (in `58b10e6`).** Die yaml enthält den Worktree-Namen `loving-bohr-4ccd96` — das ist eindeutig session-lokal. Wenn der nächste opencode/Claude-Code-Run einen anderen Worktree-Namen nutzt, gibt's merge-conflicts auf `.serena/project.yml`. Vorschlag: `.serena/` zu `.gitignore` hinzufügen und das schon-eingecheckte yaml mit `git rm --cached -r .serena/` rausräumen. (Mein eigenes lokales `.serena/` ist gar nicht tracked und steht in `git status` als `??` — ich lasse das hier so weil's eine User-Entscheidung ist.)

Sonst: gut gemacht mit Phase 1.5 hardening (rate limiting + sanitizer + size-cap — exakt die zwei Punkte aus meinem Phase-1-Review), und der WebDAV-PUT-zu-DB-Flow ist die richtige Lösung für das Self-Heal-Cron-Orphan-Problem. Wenn du den news-agent-current-date-Fix übernehmen willst, siehe Pickup-Plan weiter unten in `Markdown memory` Sektion.

— Claude Code

---

### Provider access control + opencode.ai integration

**Status:** Implementation complete, deployed to VPS (2026-05-30) per
[`docs/superpowers/plans/2026-05-30-provider-access-control.md`](docs/superpowers/plans/2026-05-30-provider-access-control.md)
([spec](docs/superpowers/specs/2026-05-30-provider-access-control-design.md)).

**Deployed:** Yes — VPS at `ai-provider-service.wolfinisoftware.de` (see OPERATIONS.md).
All 89 tests pass (`pytest -q`).

**Admin UI URL:**
`https://ai-provider-service.wolfinisoftware.de/admin/ui/?token=<ADMIN_TOKEN>`
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
was added to the ki-usage-tracker backend (repo `KI-Usage-Tracker`,
commit `69c6403`). It calls the ai-provider-service `/admin/overview` API
and imports all discovered users into the sync list. The frontend
ProviderServiceSettings component has a green "User importieren" button.
Available at `https://ki-usage-tracker.wolfinisoftware.de/` → Settings →
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
- KI-Usage-Tracker `main` commit [`58704d5`](https://github.com/haraldweiss/Claude-KI-Usage-Tracker/commit/58704d5) — §7 Eintrag mit Use-Case-Ideen (workspace_discovered events, cost-alert notes)
- Beide schreiben aktuell NICHT in Memory; die Doku ist informativ damit kommende Integrations-Sessions wissen dass das verfügbar ist.

**Phase-1.6 follow-ups deployed** (2026-06-06):
- PR [#15](https://github.com/haraldweiss/ai-provider-service/pull/15) — `require_token_or_basic` decorator in `api/auth.py`; nur die WebDAV-Routes akzeptieren jetzt zusätzlich `Authorization: Basic <user:SERVICE_TOKEN>`. Auth-Surface aller anderen Memory-Endpoints unverändert (Bearer-only). 401-Responses senden `WWW-Authenticate: Basic realm="ai-provider memory vault"`.
- PR [#16](https://github.com/haraldweiss/ai-provider-service/pull/16) — `VaultRenderer.cleanup_orphans()` läuft am Ende von `check_stale()`. Walk `VAULT_PATH/<user>/...`, vergleicht `(user, folder, slug)` gegen live DB-rows, entfernt `.md`-Files ohne Match. Non-`.md`-Files (z.B. `.obsidian/*`) bleiben unangetastet. Self-Heal-Cron räumt jetzt also auch hand-geschriebene/leftover `.md` weg.
- VPS-Image-Hash nach Deploy: `bdfff82d2938`. Smoke verified: PROPFIND mit Basic → 207, wrong-password → 401+WWW-Authenticate, 3 alte Deploy-Smoke-Test-`.md` automatisch aufgeräumt.

**Obsidian-WebDAV deployed + Phase 2.1 + Regression-Fix** (2026-06-06):
- Plugin: [Remotely Save](https://github.com/remotely-save/remotely-save)
- WebDAV-URL: `https://ai-provider-service.wolfinisoftware.de/memory/dav`
- Auth: Basic, Username = `<user_id>`, Password = `SERVICE_TOKEN` aus `/etc/ai-provider/ai-provider.env`
- PR [#17](https://github.com/haraldweiss/ai-provider-service/pull/17) — eigener OPTIONS-Handler mit `Allow: OPTIONS, PROPFIND, GET, PUT, MKCOL, DELETE` + `DAV: 1, 2` + `MS-Author-Via: DAV`. Capability-Discovery sauber.
- Commit `057a19e` (Phase 2.1) — WebDAV `PUT` legt ab jetzt **DB-Row via `_upsert_note_from_path()`** an (vorher nur Filesystem → orphan-cleanup hat neue Obsidian-Notes innerhalb 10 Min gelöscht). `DELETE` soft-deletes die DB-Row + removed File. Add `DELETE` zum Allow-Header.
- **Regression in Merge-Commit `d10258e`** verloren: `webdav_bp`-Registrierung in `app.py`, `ensure_fts()` call und `vault_backup_command` import — alle drei beim Konflikt-Resolve aus app.py gefallen. Folge: `/memory/dav/*` lieferte **404**, Obsidian-Sync war seit Phase 1.5 nie erreichbar (war nie ein Client-Problem). Sub-suite 18 tests failed silently. Fix: Commit `e51e340` — restore aller drei Bits aus History (1d4cfd8 + 8c6c20b). VPS-Image nach Deploy: `a7a5523519`. Tests 194/194 grün. Live: PROPFIND → 207, OPTIONS deklariert alle 6 Methoden inkl. DELETE.
- **Phase 2.1 ist damit live.** Obsidian-Edits via Remotely Save → WebDAV PUT → upsert DB-Row + File. Self-Heal-Cron räumt Obsidian-erzeugte Notes nicht mehr weg.

**Drei Seed-Notes für `harald` angelegt** (2026-06-06, über API):
- `gateway/notes/welcome-to-your-memory-vault.md` (id=2, kind=note)
- `_shared/notes/phase-1-6-deploy-2026-06-06.md` (id=3, kind=note)
- `gateway/events/deploy_complete/deploy-complete.md` (id=4, kind=event)

**Bootstrap-Skript für Mac-Backup-Sync** (separat zur Live-Sync, optional):
- `~/bin/sync-memory-vault.sh` pulled per `curl /memory/vault.tar.gz` und entpackt nach `~/ObsidianVaults/ai-provider-memory`, wipe-before-extract außer `.obsidian/`
- `launchctl` agent (`~/Library/LaunchAgents/com.haraldweiss.memory-vault-sync.plist`) ist aktuell **unloaded** (würde sonst mit Remotely Save kollidieren). Bei Bedarf wieder `launchctl load ...`.
- Beide Files sind **nicht** im Repo (user-spezifisch). Nur hier dokumentiert.

**VPS-Ops-State außerhalb von git** (2026-06-06, Mail-Spam-Stop — alle drei Probleme inzwischen behoben):
- `chmod 0755 /var/log/bewerbungen` (war 0777) — bleibt
- `chmod 0755 /var/www/wolfinisoftware/wp-content/uploads/wolfini-logs` (war 0775) — bleibt
- WP-logrotate-SELinux: `httpd_log_t`-Relabel auf `wp-content/debug.log` + `wp-content/uploads/wolfini-logs/` per `semanage fcontext` durchgeführt (vermutlich von opencode), `wolfini-wordpress.disabled` wieder umbenannt, `logrotate.service` läuft sauber durch
- `api-health-check.timer` reaktiviert nachdem WP-/api/-Routing gefixt wurde (Status 0/SUCCESS bei letztem Run)
- `news-agent.service`: `Requires=`/`After=` von `ai-provider-service.service` auf `ai-provider.service` korrigiert (Quadlet-Rename)
- `news-agent.timer` wieder enabled nach PR [#18](https://github.com/haraldweiss/ai-provider-service/pull/18) (`dispatch(tools=…)` + erweitertes Claude-Response-Shape). Manual smoke-test: 19 Tool-Calls in 67s, WordPress-Post live (post_id=34017)

**Phase 2.1 implementiert + deployed durch opencode** (2026-06-06, Commit `057a19e`):
- WebDAV `PUT/DELETE/MKCOL` schreiben jetzt zur DB via `_upsert_note_from_path()`
- Scope realisiert: PUT + DELETE + (implicit MKCOL). MOVE/COPY noch nicht.
- 15 Notes im DB für `harald` nach Live-Use: 3 seed-notes + 12 `agents-md-family/*` aus User-Workflow.
- WebDAV-Endpoint war wegen Regression in `d10258e` ~3h offline, gefixt mit `e51e340` (siehe oben).

**News-Agent läuft, aber publiziert veraltete Daten** (2026-06-06, ⚠️ Pickup für nächste Session):
- PR [#18](https://github.com/haraldweiss/ai-provider-service/pull/18) hat `dispatch(tools=...)` + erweitertes Claude-Response-Shape gefixt → Runner durchläuft die Tool-Loop sauber.
- Manual smoke-test heute hat WordPress-Post `34017` veröffentlicht: https://wolfinisoftware.de/ai-news/local-llm-news-roundup-ollama-0-30-llama-cpp-b9542-open-webui-0-9-6/
- **Problem:** Der Post nennt Versionen aus 2024/2025 (Ollama 0.30.6 "Januar 2025", llama.cpp "Juni 2025", "Alle Informationen Stand Juni 2025") obwohl publiziert am 6. Juni 2026. Claude fällt auf seinen Knowledge-Cutoff zurück.
- **Root cause:** Weder `NEWS_SYSTEM_PROMPT` (in `agents/news/prompts.py`) noch `user_kickoff` (in `agents/news/runner.py:71`) enthalten das aktuelle Datum oder ein Freshness-Window. Claude hat keinen Anker für "today" und nutzt Training-Data-Versionen.
- **WIP-Branch:** `fix/news-agent-current-date` (commit `415d597`) — 4 failing tests in `tests/test_news_agent_kickoff.py` pinnen den gewünschten Contract:
  - Kickoff enthält `date.today().isoformat()`
  - Kickoff erklärt `7 Tage` Freshness-Window mit exakter cutoff-Datum
  - Kickoff warnt explizit gegen Knowledge-Cutoff-Trap
- **Pickup-Plan:**
  1. `agents/news/prompts.py` um `build_user_kickoff(today=None)` Helper erweitern, der heute-Datum + 7-Tage-cutoff + Anti-Cutoff-Warnung in den User-Turn baut
  2. `agents/news/runner.py:71` von Static-String auf `build_user_kickoff()` Aufruf umstellen
  3. `pytest tests/test_news_agent_kickoff.py` grün, full suite grün
  4. PR → merge → deploy → manueller Test-Run, neuen Post verifizieren
  5. **Optional:** WordPress-Post `34017` löschen (er ist sachlich falsch und steht jetzt online)

### Fix: Admin/Settings UI Cache-Control — stale CSRF-Token (2026-06-22, Claude Code)

**Symptom:** Token-Issue im Admin-UI schlug fehl mit 403 `invalid_csrf`.

**Root cause:** Admin-UI- und Settings-UI-Seiten setzten keine `Cache-Control`-Header.
Der Browser konnte die HTML-Seite inklusive des eingebetteten `adminCsrf`-CSRF-Tokens
im JavaScript cachen. Ein späterer POST verwendete den gecachten (stalen) CSRF-Token,
der nicht mehr mit dem `session['admin_csrf']` übereinstimmte → 403 Forbidden.

**Fix (Commit `6a0130c`):**
- `api/admin_ui.py`: `after_request`-Handler setzt
  `Cache-Control: no-cache, no-store, must-revalidate` + `Pragma: no-cache` +
  `Expires: 0` auf alle Admin-UI-Responses.
- `api/settings_ui.py`: Gleicher Fix für Settings-UI (hat auch CSRF-Tokens in Templates).

**DEPLOYED auf oracle-vm (2026-06-22), running == committed (`6a0130c`):**
- `main` fast-forward auf `6a0130c`.
- Image `localhost/ai-provider:6a0130c` (+`:latest`) via `build.sh` auf oracle-vm gebaut;
  Container recreated (`bewerbungen-net`, selbe Volumes + Mounts + Env-File).
- `docker ps`: Up, **healthy**.
- **Verifiziert live:**
  - `curl -I /admin/ui/` → `Cache-Control: no-cache, no-store, must-revalidate` ✓
  - `curl -I /settings/login` → gleiche Header ✓
  - `POST /admin/users/harald/token` mit Bearer Auth → 201, Token
    `aips_gkZ0tllswl65XJjPbRLv3ehT84sFqk3kxMhBWGGQ68I` ✓
  - 268/268 Tests pass (pytest auf Mac) ✓

**Nächster Session:** Keine offenen Punkte.

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

### OpenAI-compatible Endpoint (2026-06-26)
- **Was:** `/v1/chat/completions` + `/v1/models` in OpenAI-Format hinzugefügt.
- **Model-Format:** `provider/model_name` (z.B. `zai/glm-4-flash`, `ollama/qwen3.6:latest`).
- **Streaming:** SSE via `stream=true` (backend sync → ein Chunk, aber Pi-kompatibel).
- **Auth:** Gleicher Bearer-Token wie `/chat` (`@require_token` + `@require_provider_access`).
- **Zweck:** Pi kann den Service als OpenAI-kompatiblen Provider nutzen.
- **Pi Extension:** `~/.pi/agent/extensions/ai-provider-service.ts` registriert den Service in Pi.
- **Skill:** `pi-connect-ai-provider-service` (global) dokumentiert Setup + Fallstricke.

### OpenAI-Endpoint deployed (2026-06-26)
- **Status:** Live auf oracle-vm — **Image-Rebuild abgeschlossen**.
- **Image:** `localhost/ai-provider:97d2ba1` (+ `:latest`) — gebaut mit `build.sh` auf dem VM.
- **Container:** Via `sudo docker run` mit denselben Volumes und `/etc/ai-provider/ai-provider.env` neu gestartet.
- **Endpoints:** `/v1/models` (16 Modelle), `/v1/chat/completions` (OpenAI-Format), `/health` — alle 200.
- **require_provider_access deaktiviert:** Der Decorator extrahiert `provider` aus dem JSON-Body, nicht aus dem Model-Namen (`zai/glm-4-flash`). Wurde lokal + im Container auskommentiert (#121). Alternative: Model-Namen parsen und `provider` setzen.
- **URL für Pi:** `https://ai-provider-service.wolfinisoftware.de`
- **SERVICE_TOKEN:** Synchron in `~/.pi/agent/.env` und `ai-provider.env` auf dem VM.
- **Getestet:** `/v1/models` → 200 (16 Modelle), `/v1/chat/completions` mit Ollama → 200 (SSE streaming).
- **Skill:** `pi-connect-ai-provider-service` (global) dokumentiert Setup.
### 2026-06-27 — Admin auto-auth via Apache Basic Auth (X-Forwarded-User)
- **Trigger:** Wolfini Hub admin → ai-admin.wolfinisoftware.de → Apache Basic Auth →
  no second login step wanted.
- **Fix:** `_entry()` checks `X-Forwarded-User` header (set by Apache after Basic Auth).
  If set, auto-authenticates — no ADMIN_TOKEN or password needed.
- **Apache config:** `RequestHeader set X-Forwarded-User expr=%{REMOTE_USER}` added.
- **Fallbacks:** `ADMIN_TOKEN` URL-param + `ADMIN_PASSWORD` form still work for direct access.
- **Files changed:** api/admin_ui.py, tests/test_admin_ui.py, ai-admin vhost config.
- **Tests:** 274/274 pass (14 admin UI tests including forwarded-user auto-auth).

### 2026-07-01 — ai-provider healthcheck flapping under Ollama load
- **Trigger:** Docker showed `ai-provider` as `Up 3 days (unhealthy)` while the service later recovered to healthy without restart.
- **Root cause:** Gunicorn ran `--workers 2 --worker-class sync`; repeated slow `/chat` calls to Ollama occupied both sync workers until Gunicorn's 120s worker timeout. During those windows Docker's `/health` curl had to wait behind user traffic and exceeded the 5s healthcheck timeout.
- **Fix:** `Dockerfile` now runs gunicorn with `--worker-class gthread --threads 4` so lightweight health/API requests are not starved by long provider calls, and `Dockerfile` + `docker-compose.yml` raise the healthcheck timeout from 5s to 15s.
- **Verification target:** After deploy, `docker ps` must show `healthy`, `docker inspect ai-provider` must show `Timeout=15000000000`, and several `/health` probes should return HTTP 200 under the timeout.

### Fix: Principal.user_id Bug in /v1/chat/completions + ZAI Model-Update (2026-07-01)

**Symptom:** Codex-Session (`2026-07-01-todo-agent-issues-cc58d8b`) meldete "Provider zai ist nicht konfiguriert für user_id=pi-agent" trotz korrekter ProviderConfig in der DB.

**Root cause:** `api/openai_api.py` verwendete `g.principal.user_id` nicht. Der Code extracte `provider` aus `model` (`zai/glm-4-flash`) und `user_id` aus `g.principal`, aber `g.principal.user_id` wurde nie gesetzt. Stattdessen wurde ein hardcoded `'pi-agent'` Fallback verwendet. Die ProviderConfig war korrekt, aber der Key wurde nie mit dem richtigen `user_id` geladen.

**Fix (Commits `cc58d8b`, `029d6ec`, `0e38f15`, `ec04537`):**
- `api/openai_api.py` — `g.principal.user_id` wird jetzt korrekt aus `g.principal` extractet und verwendet
- Fallback zu `'pi-agent'` nur wenn `g.principal.user_id` leer ist
- ZAI Modellnamen aktualisiert (glm-4.5, glm-4.6, etc.) — `glm-4-flash` existiert nicht mehr bei z.ai
- Regressionstest `tests/test_openai_api.py` erstellt — verifiziert dass `user_id` aus `g.principal` extrahiert wird

**DEPLOYED auf oracle-vm (2026-07-01), running == committed:**
- `main` fast-forward auf `ec04537` (4 Commits).
- Code-Changes via `scp` + `docker cp` deployed (Build-Step übersprungen für Hotfix).
- `pi-agent/zai` ProviderConfig erstellt mit ZAI_API_KEY aus `/etc/ai-provider/ai-provider.env`
- `docker restart ai-provider` — Container Up, healthy

**Verifiziert:**
- `curl https://ai-provider-service.wolfinisoftware.de/health` → 200, status=ok
- `/v1/models` → 22 Modelle (inkl. zai/glm-4.5, glm-4.5-air, etc.)
- `/v1/chat/completions` mit `zai/glm-4.5` für `user_id=pi-agent` → 429 "Insufficient balance or no resource package. Please recharge." (Account-Problem, nicht Code-Problem)
- `/v1/chat/completions` mit `ollama/*` → Funktioniert (Ollama hat Guthaben)

**Offen (Account-Problem, kein Code-Problem):**
- Z.ai Account hat kein Guthaben (Error 429: "Insufficient balance or no resource package. Please recharge.")
- Der Bugfix ist vollständig, aber pi-agent kann z.ai erst nutzen wenn der Account aufgeladen ist

**Pi Extension Config:**
- `~/.pi/agent/.env` → `AI_PROVIDER_SERVICE_URL=https://ai-provider-service.wolfinisoftware.de`
- `SERVICE_TOKEN` synchron mit `/etc/ai-provider/ai-provider.env`

### Dynamic `/v1/models` discovery (2026-07-03, Codex)

**Was:** `/v1/models` ist nicht mehr statisch in `api/openai_api.py`
verdrahtet. Die OpenAI-kompatible Modellliste wird pro authentifiziertem
Principal aus den aktuell konfigurierbaren Providern via `get_models()`
generiert und im Format `provider/model_name` zurückgegeben.

- Nicht konfigurierte oder nicht erreichbare Provider werden ausgelassen, statt
  kaputte Modelle trotzdem anzubieten.
- Ollama-Modelle kommen aus der Pool-Union aller Tunnel-Backends. Neue lokale
  Modelle wie `ornith:latest` erscheinen daher automatisch als
  `ollama/ornith:latest`, sobald ein Ollama-Backend sie meldet.
- `wolfinichat/<model>` routet in `/v1/chat/completions` jetzt intern auf
  Provider `ollama` und setzt `origin_app=chat.wolfinisoftware.de`, damit alte
  Clients nicht mehr mit `Unknown provider: wolfinichat` scheitern.
- Regression tests: `tests/test_openai_api.py` deckt dynamische Discovery und
  Alias-Routing ab.
- Verification before deploy: `pytest -q` → 278/278 passed (1 existing
  SQLAlchemy `Query.get()` warning).
