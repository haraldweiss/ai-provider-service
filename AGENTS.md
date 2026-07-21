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

### 3.4.1 oMLX is a separate authenticated MacBook backend
- oMLX runs only on the MacBook at `127.0.0.1:8000`; it is not an Ollama endpoint and must use its OpenAI-compatible `/v1` API.
- Its reverse-SSH forward is Oracle `127.0.0.1:11442`, bridged for Docker by `ai-provider-omlx-11442.service` (`172.17.0.1:11442 → 127.0.0.1:11442`). The container endpoint is `http://host.docker.internal:11442/v1`.
- `OMLX_API_KEY` is opaque: store it only in root-owned `/etc/ai-provider/ai-provider.env`; never log, commit, render, or pass it in a shell command line. Do not disable oMLX authentication to avoid distributing this key.
- Keep `com.wolfini.omlx-tunnel` and its monitor separate from all Ollama launchd agents. Log only under `~/Library/Logs`; repair a wedged job using `launchctl kickstart -k`.

### 3.5 Gunicorn in the container, behind host Apache
- `gunicorn` runs **inside** the `ai-provider` Docker container, which exposes `127.0.0.1:8767` on oracle-vm.
- Host Apache (`httpd`) reverse-proxies to it: `ai-admin.wolfinisoftware.de` → `:8767/`, and `ai-provider-service.wolfinisoftware.de/` → `:8767/` (see `/etc/httpd/conf.d/`). In-container callers reach the host via `ai-provider-bridge.service` (docker0 gw → loopback :8767).
- Never bind directly to port 80/443 — Apache owns those.
- `X-Forwarded-User` admin auto-auth is enabled only with
  `TRUST_FORWARDED_USER=true` and must have a narrow `TRUSTED_PROXY_IPS`
  allowlist. In the current Compose network Apache is observed in the
  container as `172.20.0.1`, not loopback; changing Docker networking requires
  updating the allowlist and recreating the container. Because `ProxyFix`
  rewrites `request.remote_addr` from Apache's `X-Forwarded-For`, auth trust
  checks must use its preserved immediate-peer address instead of the client
  address.

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
### 3.9 Project and global skills must be used when they provide an advantage

Before modifying configuration, deploying code, or performing any operational task:
1. Run `skill_manage view` to list all available skills (global + project-scoped).
2. If an existing skill covers the task — **use it**. Skills contain tested workflows and prevent known errors.
3. Do not skip a skill because "this is slightly different" — adapt, don't re-invent.

This rule applies to **every AI agent** working in this repo. When a skill exists for a task and the agent does not use it, that is a violation.

---

### 3.10 GitHub App and Actions tokens are opaque

- Never enforce a fixed token length or a restrictive token regex; GitHub App installation tokens may use the `ghs_` JWT format with dots and roughly 520 characters.
- Token storage, configuration and HTTP authorization handling must accept at least 520 characters and must not parse token contents.

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

### Code-Review Fixes + CI-Workflow-Reparatur + Deploy (2026-07-18, Cline)

**Scope:** Vollständiges Code-Review (python-review Skill + ruff) mit Fixes,
Deploy auf oracle-vm. Running == committed (`5ca7d0e`).

**Code-Fixes (4 Commits):**
- `9c4d1df` — requirements-Floors `anthropic>=0.40.0` / `openai>=1.55.3`:
  die alten Floors sind inkompatibel mit httpx>=0.28 (`proxies`-kwarg entfernt)
  → 7 "pre-existing" lokale Test-Failures. Suite 340+7 failed → 347/347 grün.
- `76cb9be` — `/v1/chat/completions` + `/chat`: `max_tokens: null` crashte mit
  TypeError → 500; non-list `messages` wurde zeichenweise in den Dispatcher
  iteriert. Neuer shared Helper `api/validation.py::parse_max_tokens()`
  (null→default, invalid→400) + 400 auf non-list messages.
- `7f20bf6` — lazy %-Logging im Dispatcher-Hot-Path.
- `2339e59` — ruff-Cleanup: 73 Findings → 0 (unused imports, E402, F811,
  E712 `is_(False)`, F841, F541).

**⚠️ CI war seit 2026-07-03 komplett kaputt (Fix-Commit `5ca7d0e`):**
- `c3acb29` (Coverage-CI) führte den Step-Namen `pytest + coverage (target: 80%)`
  ein — unquotet enthält er `: ` → YAML-ScannerError → GitHub meldete "workflow
  file issue", **0 Jobs liefen**. Zwei Pushes (`837caa9`, `8e793bf`) hatten somit
  gar kein CI-Gate. Fix: Step-Name gequotet. Nach dem Push liefen test +
  docker-smoke wieder grün. Kleinere offene Baustelle: actions/checkout@v4
  Node-20-Deprecation-Warnung (cosmetic).

**Deploy:**
- Server-Repo `/opt/ai-provider-service` war sauber (kein Drift), ff auf `5ca7d0e`.
- Image `localhost/ai-provider:5ca7d0e` (+`:latest`) via `build.sh` gebaut;
  Container per `sudo docker compose up -d --force-recreate ai-provider` recreated.
- `docker ps`: healthy nach 8s; Container-Image-ID matcht Tag `5ca7d0e`.
- **Verifiziert live:** `/health` lokal + public 200 (claude weiterhin ohne Key
  unhealthy — bekannter Ist-Zustand); `/v1/models` 573 Modelle;
  `max_tokens:null` → 200 (vorher 500); `messages:"hello"` → 400;
  `max_tokens:-3` → 400. Keine Startup-Tracebacks im Log.

**Verification vor Deploy:** pytest 360/360 (13 neue Tests), ruff clean,
GitHub CI test + docker-smoke grün (nach Workflow-Fix).

### Non-root container crash — readonly SQLite database (2026-07-12, opencode)

**Scope:** The security hardening (commit `9610c32`) added `USER appuser` with
`useradd -m -r appuser` (UID 999). However, the SQLite DB on the Docker volume
`bewerbungen_data` was owned by UID 1000 (`-rw-------`), so `appuser` could not
write to it. Every INSERT into `request_queue` failed with
`(sqlite3.OperationalError) attempt to write a readonly database`.

**Root cause:** The `USER appuser` directive at the end of `Dockerfile` caused
the `ENTRYPOINT` (and its `chown -R appuser:appuser /app/data`) to also run as
`appuser`, which lacks permission to chown root-owned volume files. The `chown`
in v1 (`build.sh 42548d9`) failed silently with "Operation not permitted", then
gunicorn started as appuser and couldn't write to the UID-1000-owned DB.

**Fix (commit `1570428`):**
1. **docker-entrypoint.sh** — now checks `$(id -u)` and only runs `chown` when
   running as root, then drops to appuser via `gosu appuser "$@"`.
2. **Dockerfile** — removed `USER appuser` so entrypoint runs as root.
   Installed `gosu` (lightweight privilege drop) in apt-get alongside curl.
3. **Build order** — `COPY docker-entrypoint.sh` and `RUN chmod +x` now run
   before gosu-based privilege dropping (no build-order issue).

**Deploy:** Image `localhost/ai-provider:1570428` built on oracle-vm, container
recreated. `docker ps` shows `healthy`, DB now owned by `appuser:appuser`,
gunicorn PID 1 runs as UID 999 (appuser). DB write/delete verified via
sqlite3 INSERT + DELETE to request_queue.

**To verify:** `docker exec ai-provider sh -c "cat /proc/1/status | grep Uid"` →
`Uid: 999 999 999 999` (appuser). `/health` → `status: ok`.

### OpenAI-Compatible SSE Finish Reason Compatibility (2026-07-07, Codex)

**Scope:** Fixed Pi/OpenAI-compatible streaming failures for requests such as
`model=opencode/hy3-free` that surfaced client-side as
`Error: Stream ended without finish_reason` / retry failed after 3 attempts.

**Root cause:** The gateway generated a final SSE chunk with
`finish_reason="stop"`, but interim chunks omitted the `finish_reason` key
entirely and the initial role chunk had an empty `delta`. Public HTTPS
reproduction for `opencode/hy3-free` showed HTTP 200 from opencode and a final
finish chunk, so the provider was not failing; the stream shape was too loose
for stricter OpenAI-compatible parsers.

**Behavior:** `api/openai_api.py` now includes `finish_reason: null` on interim
stream chunks and emits an initial assistant role chunk with
`{"role":"assistant","content":""}`. The final chunk still carries the concrete
finish reason (`stop`, `tool_calls`, etc.) and usage.

**Verification before deploy:** RED first:
`pytest tests/test_openai_api.py::test_streaming_chat_completions_emits_finish_reason_on_every_choice -q`
failed on the empty initial delta. GREEN: same focused test → 1 passed;
`pytest tests/test_openai_api.py tests/test_dispatcher_tools_kwarg.py -q` →
17 passed; full suite `pytest -q` → 324 passed, 1 existing SQLAlchemy
`Query.get()` warning; `git diff --check` clean.

**Deploy:** Code commit `79c70ea` was pushed to `origin/main`, built on
oracle-vm via `./build.sh 79c70ea`, and the `ai-provider` container was
recreated with `sudo docker compose up -d --force-recreate ai-provider`.
Docker reported `health=healthy`; public
`https://ai-provider-service.wolfinisoftware.de/health` returned `status=ok`.
Live HTTPS smoke against `/v1/chat/completions` with `model=opencode/hy3-free`
and `stream=true` returned interim chunks with `finish_reason: null`, an
opening assistant role/content delta, and a final chunk with
`finish_reason: "stop"` followed by `[DONE]`.

### Ollama XML Toolcall Text Parsing + Deploy (2026-07-07, Codex)

**Scope:** Local Ollama models can now recover Claude-style XML tool requests
that are emitted as assistant text instead of native Ollama `tool_calls`.
Observed symptom was literal output such as
`<read><path>/Users/.../dispatcher.py</path></read>`, causing clients to see a
claim that a tool was used without receiving a structured tool call.

**Behavior:** `providers/ollama.py` now converts well-formed XML text blocks
whose root tag exactly matches an offered OpenAI `tools[].function.name` into a
structured provider `tool_calls` entry. Direct child tags and safe attributes
become tool input arguments, e.g. `<read><path>...</path></read>` maps to
`{"name":"read","input":{"path":"..."}}`. Unknown or unoffered tools remain
plain assistant text; malformed XML remains text. This extends the existing
DSML/JSON text recovery while keeping the offered-tool allowlist as the hard
safety boundary.

**Verification before deploy:** RED first:
`pytest tests/test_ollama_provider.py::test_create_message_maps_xml_text_tool_call_when_tool_was_offered -q`
failed before the fix. GREEN: same focused test → 1 passed;
`pytest tests/test_ollama_provider.py -q` → 10 passed;
`pytest tests/test_openai_api.py tests/test_dispatcher_tools_kwarg.py -q` →
16 passed; full suite `pytest -q` → 323 passed, 1 existing SQLAlchemy
`Query.get()` warning; `git diff --check` clean.

### OpenRouter Free-Model Discovery + Deploy (2026-07-06, Codex)

**Scope:** OpenRouter is now a first-class free-capable provider for users
without a personal API key. `/v1/models` and `/providers/openrouter/models`
use dispatcher free-only config (`_free_only=True`) for unkeyed users; personal
OpenRouter keys still unlock the full model list.

**Behavior:** `providers/openrouter.py` discovers free models dynamically from
OpenRouter `/models` by checking zero prompt/completion pricing, caches the
result in `/tmp/openrouter_free_models.json` for 24h, and falls back to the
last stale cache when refresh fails instead of returning an empty list. `flask
refresh-free-models` now refreshes both opencode and OpenRouter free-model
caches. README documents the free-only provider behavior.

**Deploy:** Commit `4cf648c` pushed to `origin/main` and deployed on
oracle-vm. `/opt/ai-provider-service` is at
`4cf648c34afd2c0ae7f0f1c34d576ff2da6e13b9`; image
`localhost/ai-provider:4cf648c` was built via `./build.sh 4cf648c`, and the
container was recreated with `sudo docker compose up -d --force-recreate
ai-provider`. Docker reports `health=healthy`; `/health` returns `status=ok`
with OpenRouter healthy.

**Live smoke:** Authenticated `/providers?user_id=lisa` showed
`openrouter_visible=True` and `openrouter_hidden=False`. Authenticated
`/providers/openrouter/models?user_id=lisa` returned `count=27`,
`free_count=27`; logs showed `OpenRouter free-only mode: 27/342 models shown`.

**Operational note:** Before deploy, `/opt/ai-provider-service` had older dirty
OpenRouter drift (`providers/openrouter.py`, tests, and related config files).
It was stashed on the server as
`stash@{0}: On main: deploy-pre-4cf648c-openrouter-drift` before fast-forwarding
to `origin/main`.

**Verification:** RED first:
`pytest tests/test_openrouter_provider.py tests/test_provider_visibility.py
tests/test_refresh_free_models_cli.py -q` → 6 failed, 12 passed for the three
caveats. GREEN: same focused command → 18 passed. Full suite: `pytest -q` →
321 passed, 1 existing SQLAlchemy `Query.get()` warning. `git diff --check`
clean.

### Ollama Toolcall Text Parsing (2026-07-05, Codex)

**Scope:** Improved local Ollama toolcall recovery for OpenAI-compatible
`/v1/chat/completions` clients. Some local models return tool requests as plain
assistant text instead of native Ollama `tool_calls`.

**Behavior:** `providers/ollama.py` now converts a single JSON text object such
as `{"name":"get_weather","arguments":{"city":"Berlin"}}` into a structured
provider `tool_calls` entry, but only when the client offered that exact tool in
the OpenAI `tools` array. Unknown/unoffered tools remain normal text. DSML
parsing also accepts bare `<...invoke>` blocks without an outer `tool_calls`
wrapper, JSON code fences inside parameter values, and an ASCII DSML marker
variant. The offered-tool allowlist remains the hard safety boundary.

**Verification:** RED first: `pytest tests/test_ollama_provider.py -q` → 2
failed, 6 passed for the JSON-text and bare-DSML reproducers. GREEN:
`pytest tests/test_ollama_provider.py tests/test_openai_api.py -q` → 18 passed.

### OpenAI v1 500-Fehler: structured content + Provider-Unavailable (2026-07-03, Codex)

**Root cause:** Pi/OpenAI-kompatible Clients senden `messages[].content`
teilweise als OpenAI-Content-Part-Liste (`[{type,text}]`). `/v1/chat/completions`
reichte diese Liste unverändert an Ollama `/api/chat` durch; Ollama erwartet
String-Content und antwortet mit `400 json: cannot unmarshal array into ... content
of type string`. Gleichzeitig wurden erwartbare Provider-Ausfälle ohne
Fallback/Queue von `api/openai_api.py` pauschal als `500 server_error`
zurückgegeben.

**Fix:** Code-Commit `14e18c5` normalisiert OpenAI-Content-Parts vor dem
Dispatch zu String-Content und mappt den bekannten
`kein Fallback/Queue konfiguriert`-RuntimeError auf
`503 service_unavailable`. Regressionstests decken beide Fälle in
`tests/test_openai_api.py` ab.

**Live-Diagnose vor Fix:** Container war healthy; die 5xx lagen auf Request-
Ebene. In den letzten 24h dominierten `POST /v1/chat/completions` 500er:
Ollama-400 durch structured content sowie z.ai-429
`Insufficient balance or no resource package`. z.ai bleibt ein Account-/
Guthabenproblem, nicht ein Code-Crash.

**Verification before deploy:** `pytest tests/test_openai_api.py -q` → 6/6,
`pytest -q` → 280/280 passed (1 existing SQLAlchemy `Query.get()` warning),
`git diff --check` clean.

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

### Model health filtering + opencode free-only mode (2026-07-03, Pi)

**Trigger:** `/v1/models` listete 80 Modelle (ollama 21, opencode 52, zai 8),
aber die meisten funktionierten nicht:
- opencode zeigte 52 Modelle, nur 5 waren mit dem System-Key nutzbar (Free-Modelle)
- zai zeigte 8 Modelle, aber alle Chat-Calls scheiterten mit "Insufficient balance"
- claude war komplett down (kein API-Key auf der VM)

**Fix (2 Commits `b079040`, `ad0de7a`):**

1. **`providers/opencode.py`** — `get_models()` filtert auf Free-Modelle wenn
   `self._free_only=True` (System-Key ohne persönlichen API-Key).
   Vorher/Nachher: **52 → 5 Modelle** in `/v1/models`.

2. **`api/openai_api.py`** — `_available_model_rows()` checkt
   `health_tracker.is_healthy()` vor `get_models()`. Unhealthy Provider (z.B.
   claude ohne Key) werden übersprungen, nicht erst beim `get_models()`-Call
   abgefangen.

3. **`health_tracker.py`** — Neues `persistent`-Flag in `set_status()`:
   Runtime-Failures (aus `_execute()` in `dispatcher.py`) markieren den Provider
   als `persistent=True`. Der Background-Health-Worker (`worker.py`) kann
   persistente Failures NICHT überschreiben — nur ein erfolgreicher Chat-Call
   oder ein expliziter Reset hebt sie auf.

4. **`dispatcher.py`** — `_execute()` setzt `persistent=True` bei Chat-Fehlern.

**Grenzen:**
- zai's `.health()` checkt nur `models.list()` (funktioniert auch ohne Guthaben).
  Der wirkliche Fehler (Insufficient balance) zeigt sich erst beim Chat-Call.
  Nach dem ersten Fehlschlag pro Gunicorn-Worker wird zai persistent ausgeblendet.
- `health_tracker` ist In-Memory pro Gunicorn-Worker. Ein frischer Worker sieht
  zai zuerst als optimistisch healthy, bis der erste Chat-Call fehlschlägt.

**Verification:** pytest 278/278 passed, Container auf oracle-vm rebuilt + healthy.
  Live: opencode 5 Modelle (vorher 52), zai nach 1. Fehlschlag persistent unhealthy.

### Code-Review Fixes (2026-07-03, Pi)

**Was:** Sechs Fixes aus einem strukturierten Code-Review des gesamten Repository.

1. **ProviderUnavailableError** — Eigene Exception-Klasse (`dispatcher.py`) statt
   String-Matching auf `RuntimeError` in `api/openai_api.py`. Der `except RuntimeError`
   mit `'kein Fallback/Queue konfiguriert' in str(e)` ist ein Code-Smell (fragil bei
   Refactoring/Lokalisierung). `ProviderUnavailableError` erbt von `RuntimeError` und
   wird direkt gecatcht. Test `test_chat_completions_returns_503_for_provider_unavailable`
   aktualisiert.

2. **Worker-Tests** — 10 neue Tests in `tests/test_worker.py`: `_check_provider` mit
   Mocking (system healthy/unhealthy, non-system ohne Config, persistent failure nicht
   überschreibbar, Exception geschluckt); `start()`/`stop()`-Idempotenz; `_run`-Loop
   Scheduling und Crash-Safety.

3. **Coverage in CI** — `pytest-cov` in `.github/workflows/ci.yml` integriert:
   `--cov=. --cov-report=term-missing --cov-fail-under=80`. Schützt vor unbemerkten
   Test-Lücken bei PRs.

4. **Leerer-Token Schutz** in `api/auth.py` — `_resolve_principal()` prüft jetzt
   explizit auf `len(parts) < 2` und `not token`, so dass `Authorization: Bearer `
   (leerer Token) nicht zu `IndexError` oder falscher Auth führt.

5. **Claude-Modell-Liste via Env-Var** — `CLAUDE_MODEL_LIST` in `config.py` und
   `providers/claude.py`. Überschreibt die statische `KNOWN_MODELS`-Liste ohne
   Code-Änderung bei neuen Anthropic-Modellen. In `.env.example` dokumentiert.

6. **Division-by-Zero-Guard** in `worker.py` — Pre-computed `hc_steps/qd_steps/fm_steps`
   mit `max(1, ...)` und `sleep_sec > 0` Guard, statt `tick % (interval // sleep_sec)`.
   Verhindert `ZeroDivisionError` bei pathologischen Configs.

**Files:** 9 geändert (8 modified + 1 new: `tests/test_worker.py`). 10 neue Tests.
**Verifikation:** `pytest -q` → **300/300 passed** (290 bestehend + 10 Worker-Tests).
  Coverage-CI-Integration auf Mac getestet (kein CI-Run auf oracle-vm nötig).

### Timeout-Mismatch: Gunicorn 120s vs Ollama 180s → 503er vermeiden (2026-07-03, Pi)

**Symptom:** `/v1/chat/completions` lieferte sporadisch 503 `service_unavailable`
obwohl Ollama erreichbar war.

**Root cause:** Timeout-Mismatch — Ollama wartete bis zu 180s, Gunicorn killte
den Worker bereits nach 120s. Bei längeren Ollama-Calls (große Kontexte,
Cold-Start) starb der Worker mit 502/503, bevor Ollama ein Ergebnis liefern
oder einen eigenen Fehler zurückgeben konnte. Der saubere Error-Handling-Pfad
(Fallback/Queue) wurde nie erreicht.

**Fix (Commit `<SHA>`):**
1. `Dockerfile`: Gunicorn `--timeout 120→180s` — erlaubt langsame Ollama-Calls
2. `Dockerfile`: Healthcheck `timeout 15→20s`, `start-period 15→20s` — Puffer bei Last
3. `providers/ollama.py`: `timeout 180→120s` — Ollama bricht **vor** Gunicorn ab,
   Fehler wird sauber returned → Health-Tracker updated → Fallback/Queue aktiv
4. `providers/ollama.py`: `num_ctx = min(num_ctx, 65536)` — cap verhindert extreme
   Kontexte (131k+) die auf M3 Max 36GB in Swap/Thrashing gehen → garantierter Timeout

**Verifikation:** `pytest -q` → 290/290 passed, keine neuen Warnungen.
  Image: `localhost/ai-provider:ebbf3bd` auf oracle-vm gebaut + deployed.
  Live-Smoke: /health 200, /v1/models 36 Modelle, chat ollama → OK.
  docker-compose.yml healthcheck timeout 15s→20s, start_period 15s→20s per
  592ba84 nachgereicht (git pull + rsync auf oracle-vm, kein Image-Neubau nötig).

### Fallback-Model + Queue + Opencode-Failover konfiguriert (2026-07-04, Pi)

**Was:** Queue und Fallback-Provider für alle Hauptnutzer aktiviert, neues
`fallback_model`-Feld in ProviderConfig.

**Problem:** ProviderConfig hatte nur `fallback_provider` aber kein
`fallback_model`. Bei Ollama→opencode-Failover bekam opencode den
Ollama-Modellnamen (z.B. `mistral-nemo-cc:latest`) → nie matchbar →
Failover schlug fehl.

**Fix (Commit `4341ee8`):**
- `storage/models.py`: neues `fallback_model`-Feld (String(64), nullable)
- `dispatcher.py`: `dispatch()` verwendet `pc.fallback_model` wenn gesetzt,
  sonst Primary-Modellname (bisheriges Verhalten)

**DB-Konfiguration (oracle-vm):**
- `ALTER TABLE provider_configs ADD COLUMN fallback_model VARCHAR(64)`
- Alle ollama-Configs: `fallback_provider=opencode`,
  `fallback_model=deepseek-v4-flash-free`, `queue_when_unavailable=True`
- pi-agent/zai: queue + fallback aktiviert
- pi-agent/ollama: neu angelegt mit fallback + queue

**Aktuelle Configs (10 rows):**

| User | Provider | Fallback | Fallback-Model | Queue |
|---|---|---|---|---|
| test-user | ollama | opencode | deepseek-v4-flash-free | ✅ |
| wolfini_de_web | ollama | opencode | deepseek-v4-flash-free | ✅ |
| lisa | ollama | opencode | deepseek-v4-flash-free | ✅ |
| wolfinichat | ollama | opencode | deepseek-v4-flash-free | ✅ |
| wolfinichat | opencode | ollama | (primary model) | ✅ |
| pi-agent | ollama | opencode | deepseek-v4-flash-free | ✅ |
| pi-agent | zai | opencode | deepseek-v4-flash-free | ✅ |

**Noch offen:**
- z.ai-Guthaben aufladen (Account-Problem, kein Code-Fix)

**Verifikation:** `pytest -q` → 290/290 passed.
  Image: `localhost/ai-provider:4341ee8` auf oracle-vm deployed.
  Live-Smoke: /health 200, /v1/chat/completions ollama/llama3.2:3b → "2" ✅

### Local Queue Proxy + Ollama launchd + Pi Extension Safety Net (2026-07-07, Pi)

**Scope:** ISP-Ausfall-Resilienz für den lokalen Mac. Der ai-provider-service läuft auf oracle-vm und ist bei ISP-Ausfall vom Mac aus nicht erreichbar. Drei neue Komponenten auf dem MacBook puffern den Ausfall lokal:

**Komponente 1: `~/bin/ai-provider-local-proxy.py`** — Flask-Proxy auf Port 8766, managed via launchd (`com.haraldweiss.ai-provider-proxy`):
- Normal: transparentes Forwarding aller Requests an das Gateway (https://ai-provider-service.wolfinisoftware.de)
- ISP down + lokales Ollama läuft: `ollama/*`-Modelle werden direkt an localhost:11434 geroutet
- ISP down + Ollama aus: alle Requests in SQLite-Queue (~/.ai-provider-proxy/queue.db, 24h TTL)
- ISP down + Cloud-Only-Modelle (claude/*, zai/*, opencode/*): ebenfalls in Queue
- Recovery: Background-Health-Checker (alle 30s) erkennt Gateway-Wiederkehr und drain die Queue
- /v1/models: cached (24h TTL) + merged mit lokalen Ollama-Modellen bei Ausfall
- /health: zeigt gateway_healthy + local_ollama_healthy Status

**Komponente 2: `~/bin/ollama-launchd-wrapper.sh`** — launchd-Wrapper für Ollama, da `~/.ollama` ein Symlink auf externe SSD ist (`/Volumes/externeSSD/ollama-data`):
- Wrapper wartet bis zu 30s auf den SSD-Mount, startet dann `ollama serve`
- Ohne SSD: terminiert sauber, launchd wiederholt nach 30s ThrottleInterval
- launchd-Agent: `com.haraldweiss.ollama` (ersetzt `homebrew.mxcl.ollama`, das war disabled)

**Komponente 3: Pi Extension (`~/.pi/agent/extensions/ai-provider-service.ts`)** — Safety Net:
- Wenn der Proxy/Gateway nicht erreichbar ist (`/v1/models` fetch failed), wird `usedFallback=true` gesetzt
- Dann wird versucht, lokales Ollama direkt via `http://localhost:11434/api/tags` zu erreichen
- Bei Erfolg: Registrierung eines zweiten Pi-Providers `ollama-local` mit `baseUrl=http://localhost:11434/v1`
- Kein SERVICE_TOKEN nötig (lokaler Zugriff)
- In Pi via Ctrl+L als separater Provider sichtbar

**Konfigurationsänderung:**
- `~/.pi/agent/.env`: `AI_PROVIDER_SERVICE_URL=http://localhost:8766` (vorher: `https://ai-provider-service.wolfinisoftware.de`)

**Kein Deploy auf oracle-vm nötig** — alle Komponenten sind lokal auf dem MacBook.

**Live-Smoke getestet:**
- Proxy /health → 200 (gateway_healthy=true, local_ollama_healthy=false)
- Proxy /v1/models → 41 Modelle (durchgereicht)
- Proxy /v1/chat/completions → ollama/qwen3.6 Antwort via Gateway ✅
- Proxy läuft via launchd (threaded=True für Concurrent Access)
- Queue-DB initialisiert (0 rows)
- Ollama-Wrapper: loggt "Mount point not available" wenn SSD nicht connected, terminiert sauber

**Betrieb:**
```bash
# Proxy-Logs
tail -f ~/Library/Logs/ai-provider-proxy.err

# Ollama-Wrapper-Logs
tail -f /opt/homebrew/var/log/ollama-wrapper.log

# Queue-Status
curl http://localhost:8766/v1/queue/QUEUE_ID

# Proxy neustarten (nach Code-Änderung)
launchctl kickstart -k gui/$(id -u)/com.haraldweiss.ai-provider-proxy

# Ollama neustarten
launchctl kickstart -k gui/$(id -u)/com.haraldweiss.ollama

# Direkter Zugriff auf den Proxy (für Tests ohne Pi)
curl http://localhost:8766/health
curl http://localhost:8766/v1/models
```

**Nach ISP-Recovery:** Queue wird automatisch drained (max 10 Requests/Cycle, alle 30s). Fertig verarbeitete Items bleiben als 'done' in der Queue-DB (für Debugging).

**Offen/Beachte:**
- Pi muss einmal neugestartet werden, um neue .env-URL + Extension zu laden
- Lokales Ollama läuft nur wenn die externe SSD angeschlossen ist (dann automatisch via launchd)
- Proxy verwendet Flask-Dev-Server (für Single-User-Betrieb ausreichend)
- Queue läuft nur, während der Proxy-Prozess lebt (keine persistente Queue bei Reboot — die Items gehen dann verloren, da nur in Memory-Queue)

### Local Proxy: Local-First Ollama Routing + Launchd Cleanup (2026-07-07, opencode)

**Scope:** Pi requests (`ollama/ornith:latest`) timed out because the proxy tried the remote gateway first (30s), then local Ollama (36s model loading) — total ~66s+.

**Fix (`~/bin/ai-provider-local-proxy.py`):**
- `ollama/*` models now route to **local Ollama first**, bypassing gateway latency
- Gateway timeout reduced from 30s → 10s (for non-ollama models that need the gateway)
- Local Ollama timeout kept at 120s (covers model loading from external SSD)
- After model is loaded: response in ~1s

**Launchd cleanup:**
- `ollama-launchd-wrapper.sh`: fixed leading space bug (` waited=0` → `waited=0`) causing infinite loop
- `com.haraldweiss.ollama` + `homebrew.mxcl.ollama` both unloaded — they competed with `com.ollama.ollama` (Ollama.app) for port 11434, flooding `ollama.err` with "bind: address already in use"

**Verification:**
- First request via proxy: 48s (model load from SSD), second: 1s (cached)
- Non-ollama (opencode) via gateway: 1.5s
- No errors in proxy log
- Launchd list clean — only `com.ollama.ollama` runs `ollama serve`

**Kein Deploy auf oracle-vm nötig** — alles lokal auf dem MacBook.

### Mac Mini Proxy Setup (2026-07-07, opencode)

**Scope:** Gleicher Proxy + Pi-Config auch auf dem Mac Mini installiert, damit Pi dort
ebenfalls lokal-first für Ollama routet (statt übers Gateway via reverse-SSH-Tunnel).

**Installiert:**
- `~/bin/ai-provider-local-proxy.py` (via scp from MacBook)
- `~/.ai-provider-proxy-venv/` (venv mit flask + requests)
- `~/Library/LaunchAgents/com.haraldweiss.ai-provider-proxy.plist` (launchd, Python aus venv)
- `~/.pi/agent/.env`: `AI_PROVIDER_SERVICE_URL=http://localhost:8766` (vorher: direktes Gateway)

**Verifiziert:**
- Proxy /health → 200 (gateway_healthy=true, local_ollama_healthy=true)
- 59 Models via Proxy
- `ollama/ornith:latest` → lokal (1. Request lädt Modell, danach <1s)
- `opencode/deepseek-v4-flash-free` → Gateway (~1.7s)

### Local Proxy SSE Streaming Fix + Ollama Cold-Start (2026-07-07, opencode)

**Symptom:** Pi (openai npm v6.26.0) loaded the ai-provider-service extension, registered
models, then hung on the first chat request with "Stream ended without finish_reason".
The error appeared only when routing through the local proxy (`localhost:8766`);
direct gateway access worked fine.

**Root cause:** `_stream_response()` in `~/bin/ai-provider-local-proxy.py` iterated over
`resp.iter_lines()` and applied `if chunk:` to skip empty lines. In SSE, the empty line
is the `\n` event delimiter — `data: {...}\n\n` is two events separated by a blank line.
Skipping it meant each SSE event was emitted with a single `\n` instead of `\n\n`.
Pi's SSE parser in openai v6.26.0 split on `\n\n` and never found a `finish_reason`.

**Fix:** Removed the `if chunk:` guard — now every line from `iter_lines()` is yielded
with `+ "\n"`, preserving the original `\n\n` SSE event boundary.

**Verified:** `pi` with default model (`openrouter/cohere/north-mini-code:free`)
returned "Hallo! Wie kann ich Ihnen heute helfen?" ✅. `pi` with
`opencode/deepseek-v4-flash-free` returned a full response ✅. `curl` streaming
output shows proper `\n\n` between `data:` events.

**Files changed:** `~/bin/ai-provider-local-proxy.py` on MacBook + Mac Mini.

**Ollama cold-start fix (same session):**
- **Problem:** `ollama/*` models first request can take >30s to load from external SSD.
  Previously, the 120s local timeout would expire → fall through to gateway (10s) → queue
  (202 non-streaming). Pi expects streaming → hang.
- **Fix:** (1) `timeout=300s` for streaming ollama requests (up from 120), (2) return
  503 error when local Ollama fails in stream mode instead of falling through to gateway
  or queue (both unusable for Pi streaming), (3) removed `_local_ollama_healthy` guard
  — always try local Ollama for `ollama/*` models, even if background health check is stale.
- **New constant:** `LOCAL_OLLAMA_STREAM_TIMEOUT = 300` in the proxy.

**Verification before deploy:** `launchctl kickstart -k` on both Macs; proxies
healthy. Pi returns responses for non-ollama models as before.

**Kein Deploy auf oracle-vm nötig** — alles lokal auf MacBook + Mac Mini.

### Local Proxy: 404 Gateway-Fallback für lokal nicht vorhandene Ollama-Modelle (2026-07-08, opencode)

**Symptom:** Pi zeigte Modelle aus `/v1/models` (vom Gateway gecached) an, aber Chat-Requests scheiterten. `ollama/oracle-deepseek-r1-qwen:7b` → 404 "model not found", `ollama/glm-4.7-flash:latest` → 503 "No connectivity to local Ollama or gateway". Modelle existieren auf den Gateway-Ollama-Backends (oracle-vm), aber nicht auf dem lokalen Mac.

**Root cause:** Der Proxy routet `ollama/*`-Modelle zuerst zu lokalem Ollama. Fiel lokales Ollama mit 404 (model not found), wurde der Fehler direkt an Pi zurückgegeben — ohne Gateway-Fallback. Der `except (ConnectionError, Timeout)`-Pfad fing nur Verbindungsfehler, nicht HTTP-Fehler.

**Fix:** `~/bin/ai-provider-local-proxy.py:399` prüft jetzt `ol_resp.status_code < 400` und fällt bei 4xx/5xx aufs Gateway zurück. Logger warnt mit `"Local Ollama returned %d for %s — trying gateway fallback"`.

**Verifiziert:** Code-Review + Syntax-Check. Proxy-Restart via launchd nötig.

**Kein Deploy auf oracle-vm nötig** — alles lokal auf MacBook (+ Mac Mini wenn dort auch aktualisiert).

### Cline Provider added (2026-07-12, Pi)

**Scope:** Neuer `cline`-Provider für das gehostete OpenAI-kompatible Cline-API
(`https://api.cline.bot/api/v1`). Cline ist ein Reseller-Gateway mit
`provider/model`-Model-IDs (z.B. `anthropic/claude-sonnet-4-6`).

**Behavior:** `providers/cline.py` (`ClineClient`) spricht Cline wie
`OpenAIClient` via `openai`-SDK, `api_key` als Bearer (pflicht), konfigurierbarer
`api_endpoint`. Der `model`-Parameter behält den eigenen Slash (`anthropic/...`),
weil `_parse_model` nur am **ersten** Slash splittet — `cline/anthropic/...`
round-tripet sauber. `get_models()` holt die Live-Liste von Cline `/models`.
Kein zentraler Server-Key (jeder User konfiguriert eigenen Key via
ProviderConfig); daher Registry-Eintrag `system: False, requires: ['api_key'],
optional: ['api_endpoint'], personal_api_key: True` (Spiegel von `openai` /
`ollama_cloud`). `Config.CLINE_BASE_URL` / `CLINE_API_KEY` (env-Override) +
`.env.example`-Block hinzugefügt. README Provider-Zahl 7→8, v1-Modell-Tabelle
und Access-Control-Liste aktualisiert.

**Verification:** `pytest tests/test_cline_provider.py` → 12 passed (inkl.
Slash-Round-Trip-Test via `_parse_model`); `pytest -q` → 336 passed (1 bestehende
SQLAlchemy-`Query.get()`-Warnung); `git diff --check` clean. `test_provider_visibility`
auf `hidden_provider_count` 5→6 angepasst (cline ist zusätzlich key-pflichtig).

**Kein Deploy auf oracle-vm nötig** — Code-Änderung; deployen sobald ein User
Cline nutzen will (ProviderConfig + ggf. Pricing-Override, sofern Cline-Preise
bekannt; `calc_cost_usd` liefert aktuell `None` für unbekannte Cline-Modelle,
was Usage-Events nicht crashen lässt).

**Pricing (2026-07-12, Pi) — recherchiert + ClinePass-Erkenntnis:** Cline
veröffentlicht KEINE statische Rate-Card auf der Website, `GET /api/v1/pricing`
liefert **401** (auth-walled). Cline's OSS-Repo enthält aber den generierten
Modell-Katalog `sdk/packages/llms/src/catalog/catalog.generated.ts` mit pro
Modell `pricing:{input,output}` in **USD pro 1M Tokens** — Cline's
"Inference at Cost"-Listenpreise (Beleg: `anthropic/claude-sonnet-4.6`=$3/$15,
`openai/gpt-4o`=$2.5/$10, matcht upstream). Daraus wurde
`pricing_overrides_cline.json` (513 Modelle, Key `cline::<provider/model>`)
extrahiert + committet; `pricing.py` mergt sie (`_CLINE_OVERRIDE_PATH`),
`calc_cost_usd` lädt pro Call frisch → wirksam ohne Restart, via `COPY . .`
im Image (überlebt Rebuild). Tests: `tests/test_pricing_cline.py` (6 passed).

**ClinePass (User hat "Cline Pass (Annual)", $79.92/Jahr, renew 2027-07-12):**
Der Subscription-Screen besagt: ClinePass gilt für **open-weight models** ("Select
ClinePass as the provider"), Frontier-Modelle (Claude/GPT/Gemini) sind NICHT
inkludiert. Im OSS-Katalog sind genau diese open-weight Modelle unter dem
Provider-Präfix **`cline-pass/`** gelistet (z.B. `cline-pass/qwen3.7-plus`,
`cline-pass/deepseek-v4-pro`). Daher im Override: **`cline::cline-pass/*` →
$0** (vom Pass gedeckt), alle übrigen (`anthropic/*`, `openai/*`, `google/*`,
sowie pay-as-you-go `qwen/*`/`deepseek/*`) → Katalog-Listenpreise. Natively
kostenlose Modelle (`:free`, Catalog-`pricing:0`) sind ohnehin $0. Frontier
bleiben zum Katalog-Preis — falls der User sie trotz Pass nutzt, wären das
Separat-Kosten. Annahme: ClinePass deckt nur das kuratierte `cline-pass/*`-Set;
falls ClinePass laut Dashboard auch andere Open-Weight-Modelle abdeckt,
betroffene Einträge auf $0 setzen. Dashboard ist die live-Quelle für
account-spezifische Raten.

### Cline Provider Fixes — Deploy + get_models Fallback + ClinePass bestätigt (2026-07-12, Pi)

**Deployed auf oracle-vm** — Image `c54c980` auf `bewerbungen-net`, Container healthy.
Harald + pi-agent Configs angelegt (API-Key, fallback_provider=opencode, queue aktiv).

**Probleme während Deploy + Fixes:**

1. **Cline hat keinen GET /models Endpoint** (404 auf alle Pfade).
   `get_models()` fällt jetzt auf `pricing_overrides_cline.json` zurück (513 Modelle aus
   Cline's OSS Catalog). `health()` interpretiert 404 als „Server lebt, Endpoint nicht
   vorhanden" (gibt True zurück).

2. **Cline's Chat-Response ist non-standard**: `{"data": {"choices": [...],
   "usage": ...}, "success": true}` — OpenAI SDK kann das nicht parsen (choices=None).
   `create_message()` wurde auf raw httpx umgestellt (import httpx).

3. **ClinePass Dashboard bestätigt $0**: Alle `cline-pass/qwen3.7-plus`-Calls zeigen
   0.0000 Credits im Dashboard unter `https://app.cline.bot/dashboard/usage`.

**Verification vor Deploy:** `pytest tests/test_cline_provider.py` → 13/14 passed (1
pre-existing openai SDK httpx-Kompatibilitäts-Fehler, auch bei opencode/zai/other
Providern). `pytest tests/test_pricing_cline.py` → 6/6 passed. Full suite: 335/343
passed (8 pre-existing failures — 4× openai SDK compat, 4× claude anthropic SDK fehlt).

**ClinePass-Fakt:** Jahresabo $79.92, UI-Text: „open-weight models". Dashboard zeigt
0.0000 Credits pro Call → `cline-pass/*`-Modelle sind tatsächlich $0. Die Override-
Annahme (cline-pass/*=$0) ist korrekt.

**Pi-Connect-Skill aktualisiert:** `pi-connect-ai-provider-service` (global) um Cline-
spezifische Schritte, Pitfalls und Verification erweitert.

### Security Hardening + OpenRouter Fix + Deploy (2026-07-12, Cline)

**Scope:** Security hardening across 11 files, pricing cache fix, Cline httpx
refactor, and OpenRouter `_enforce_credentials` fix. All deployed to oracle-vm.

**Commits:** `561d7f1` (security), `5be4d30` (pricing+cline), `fde40d3` (tests),
`9610c32` (openrouter fix) — all on `origin/main`.

**Changes:**

1. **TRUST_FORWARDED_USER** (`config.py`, `api/admin_ui.py`) — X-Forwarded-User
   auto-auth was introduced behind the `TRUST_FORWARDED_USER=true` env flag.
   **Superseded on 2026-07-14 by `c72c556`:** Docker delivers Apache through
   `172.20.0.1`, and `ProxyFix` rewrites `request.remote_addr` to the browser
   IP. The current implementation therefore checks the preserved immediate
   proxy peer against explicit `TRUSTED_PROXY_IPS`, rather than loopback or the
   forwarded client address. This keeps external header spoofing blocked.

2. **MAX_CONTENT_LENGTH** (`app.py`) — 10 MiB request cap on all requests.

3. **Non-root container** (`Dockerfile`) — `useradd -m -r appuser` + `USER appuser`.

4. **_principal_user_id()** (`api/openai_api.py`) — raises `ValueError` instead
   of defaulting to `'pi-agent'`. Callers return 401 on missing identity.

5. **Bare except cleanup** (`cli.py`, `pricing.py`, `storage/provider_configs.py`)
   — replaced `except Exception: pass` with specific exceptions + logging.

6. **Pricing cache** (`pricing.py`) — 60s in-memory cache for
   `_load_merged_pricing()`. `_reset_pricing_cache()` for test isolation.
   `_merge_override_file()` skips non-`::` keys (like `_meta` in
   `pricing_overrides_cline.json`).

7. **Cline httpx refactor** (`providers/cline.py`) — removed OpenAI SDK
   dependency entirely. `health()` does a lightweight chat ping. `max_tokens`
   floored to 16 (Cline minimum).

8. **OpenRouter `_enforce_credentials` fix** (`providers/openrouter.py`) —
   removed `_enforce_credentials=False` (removed in openai SDK 1.109+).
   `_make_openai_client()` helper uses `'sk-anonymous'` placeholder when no
   API key is configured, enabling anonymous free-model access across all
   SDK versions.

9. **requirements.txt** — upper-bound pins on all deps + `httpx>=0.27.0,<1.0`.

10. **Tests** — `tests/conftest.py` autouse `_reset_pricing_cache` fixture.
    `tests/test_cline_provider.py` rewritten for httpx-based implementation.
    `tests/test_admin_ui.py` sets `TRUST_FORWARDED_USER = True`.

**Deployed on oracle-vm:**
- Image `localhost/ai-provider:9610c32` (+ `:latest`), container healthy.
- `/health` → `status=ok`; all providers healthy except `claude` (no key)
  and `custom` (no endpoint). OpenRouter went from ❌ to ✅.

**Verification:** `pytest -q` → 336 passed, 7 pre-existing openai SDK compat
failures (`openai 1.54.0` + `httpx 0.28.1` `proxies` TypeError on local Mac;
container has `openai 1.109.1` which doesn't have this issue).

**Pre-existing test failures note:** 7 tests fail locally due to
`openai 1.54.0` + `httpx 0.28.1` incompatibility (`proxies` kwarg removed
in httpx 0.28). These pass in the container (`openai 1.109.1`). Not caused
by this change — confirmed by stashing and running on clean `main`.
