# ai-provider-service

Multi-Provider AI-Gateway mit Fallback-Routing und Queue-Persistenz für lokale Provider (Ollama).

**Eine Provider-Verwaltung für alle Apps.** Statt jeder Konsumenten-App
(Bewerbungstracker, loganonymizer, …) eigene API-Key-Verwaltung + CORS-Handling
zu geben, läuft dieser Service einmal zentral und alle Apps fragen ihn an.

## Features

- **7 Provider** out of the box: Claude, Ollama, OpenAI, Mammouth, Custom (OpenAI-kompatibel), opencode.ai (Zen), z.ai (GLM)
- **Server-Key-Allowlist** für zentrale Provider-Keys (Claude, z.ai): der zentrale Key ist nur für gelistete User nutzbar; z.ai ist per Default auf `ADMIN_USER_ID` beschränkt — alle anderen brauchen einen eigenen Key (auch für die kostenlosen GLM-Flash-Modelle)
- **Per-User-Konfiguration** mit Fernet-verschlüsselten API-Keys
- **Fallback-Provider**: bei Nicht-Erreichbarkeit automatisch auf z.B. Claude umschalten
- **Queue-Persistenz**: bei Ollama-Ausfall werden Requests in SQLite gequeued und automatisch nachgearbeitet, sobald Ollama wieder online ist
- **Health-Monitoring**: Background-Worker pollt alle Provider regelmäßig
- **CORS-Handling** zentral (für Browser-direkt-Aufrufe)
- **Bearer-Token-Auth** für Konsumenten-Apps

## Architektur

```
┌──────────────────┐         ┌────────────────────────┐
│  Bewerbungstracker│         │  loganonymizer (Browser)│
│   (VPS Backend)  │         │                        │
└─────────┬────────┘         └───────────┬────────────┘
          │                              │
          │  POST /chat                  │  POST /chat (CORS)
          ▼                              ▼
       ┌─────────────────────────────────────┐
       │       ai-provider-service           │
       │  ┌───────────────────────────────┐  │
       │  │ Dispatcher (sync/fallback/q)  │  │
       │  └──┬───────────┬───────────┬────┘  │
       │     │           │           │       │
       │   Claude     Ollama      OpenAI…    │
       └─────┼──────────┼───────────┼────────┘
             │          │           │
        api.anthropic   │       api.openai.com
                        │
              ┌─────────▼────────────┐
              │ Mac-Localhost (Reverse-SSH-Tunnel)
              │ → 127.0.0.1:11434    │
              └──────────────────────┘
```

## Voraussetzungen

- **Python 3.9+** (auf VPS aktuell 3.12 — Rocky 9 Standard-Repos)
- SQLite (im Standard-Library-Set)
- Optional: Ollama lokal für Local-LLM-Provider

## Documentation Index

- **[README.md](README.md)** — Overview, setup, and API reference (this file)
- **[OPERATIONS.md](OPERATIONS.md)** — Production operations, monitoring, troubleshooting
- **[MIGRATION.md](MIGRATION.md)** — Integration guide for client apps
- **[INTEGRATION_TEMPLATES.md](INTEGRATION_TEMPLATES.md)** — Copy-paste code templates
- **[ROLLOUT_PLAN.md](ROLLOUT_PLAN.md)** — Phase-by-phase implementation timeline

## Local Dev Setup

```bash
git clone <this-repo>
cd ai-provider-service

python3.12 -m venv venv     # oder python3, mindestens 3.9
. venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# In .env eintragen:
#   MASTER_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
#   SERVICE_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

python3 app.py
# → Service läuft auf http://127.0.0.1:8767
```

Smoke-Test:
```bash
curl http://127.0.0.1:8767/health
```

## Deployment (oracle-vm Docker)

Der Service läuft als Docker-Container auf **oracle-vm** (Oracle Linux 9, aarch64)
hinter Apache Reverse-Proxy.

### Deploy (Quick)

```bash
cd ~/projects/ai-provider-service
# 1) Source auf oracle-vm syncen
rsync -avz --delete     --exclude='venv' --exclude='__pycache__' --exclude='.git' --exclude='.env' --exclude='node_modules'     ./ opc@oracle-vm:/tmp/ai-provider-service-build/

# 2) Per SSH auf oracle-vm deployen
ssh opc@oracle-vm
cd /tmp/ai-provider-service-build
bash deploy.sh
# → docker compose build + up -d
```

### First-Time Setup (oracle-vm)

```bash
# 1) Env-Datei anlegen
sudo mkdir -p /etc/ai-provider
sudo nano /etc/ai-provider/ai-provider.env
# → MASTER_KEY, SERVICE_TOKEN, SECRET_KEY, FERNET_KEY eintragen

# 2) Env-Datei schützen
sudo chmod 600 /etc/ai-provider/ai-provider.env

# 3) Datenverzeichnis
sudo mkdir -p /opt/ai-provider-data
sudo chown opc:opc /opt/ai-provider-data

# 4) Apache VHost (eigener VirtualHost)
# /etc/httpd/conf.d/ai-provider-service.wolfinisoftware.de.conf:
#   <VirtualHost *:443>
#     ServerName ai-provider-service.wolfinisoftware.de
#     SSLProxyEngine On
#     ProxyPreserveHost On
#     ProxyPass / http://127.0.0.1:8767/ retry=0 timeout=600
#     ProxyPassReverse / http://127.0.0.1:8767/
#   </VirtualHost>
sudo systemctl reload httpd

# 5) SSL-Zertifikat (falls nötig)
sudo certbot --apache -d ai-provider-service.wolfinisoftware.de
```

### docker-compose.yml

```yaml
services:
  ai-provider:
    build: .
    container_name: ai-provider
    restart: always
    ports:
      - 127.0.0.1:8767:8767
    volumes:
      - /opt/ai-provider-data:/app/data
      - /opt/ai-provider-data/pricing_overrides.json:/app/pricing_overrides.json
    env_file: /etc/ai-provider/ai-provider.env
    extra_hosts:
      - host.docker.internal:host-gateway  # für Ollama-Tunnel-Zugriff
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8767/health || exit 1"]
      interval: 30s
      timeout: 15s
      retries: 3
```

Gunicorn läuft im Container mit `gthread` (`2` Worker, `4` Threads pro Worker),
damit leichte Requests wie `/health` nicht hinter langsamen Ollama-Calls
verhungern.

**OLLAMA_URL:** Container-intern `http://host.docker.internal:11434` verwenden
(löst über Docker-DNS zum Host-Loopback auf — überlebt Bridge-IP-Wechsel).

### Apache-Config

Siehe `deploy/apache-vhost.conf`.

### systemd-Unit (Boot-Order)

`/etc/systemd/system/ai-provider-docker.service` (siehe `deploy/ai-provider-docker.service`)
startet den Container nach `docker.service` + `network.target`.

```bash
sudo systemctl enable ai-provider-docker.service
sudo systemctl start ai-provider-docker.service
```

### Mac (für Ollama-Tunnel)

Ollama läuft lokal auf dem Mac. Damit der oracle-vm-Container Ollama erreicht, brauchen
wir einen persistenten Reverse-SSH-Tunnel.

1) autossh installieren:
```bash
brew install autossh
```

2) SSH-Key auf oracle-vm hinterlegen (passwordless-Login):
```bash
ssh-copy-id opc@oracle-vm
```

3) LaunchAgent installieren:
```bash
cp deploy/com.ai-provider.ollama-tunnel.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ai-provider.ollama-tunnel.plist
```

4) Status prüfen:
```bash
launchctl list | grep ai-provider
# Auf oracle-vm:
ssh opc@oracle-vm 'curl -s http://127.0.0.1:11434/api/tags'
```

Der Tunnel restarted sich automatisch bei Drop / Mac-Reboot. Wenn der Mac aus ist
oder schläft, ist Ollama nicht erreichbar — der Service queued Requests dann
automatisch und arbeitet sie ab, sobald der Mac (und damit der Tunnel) wieder da
ist.

**Wichtig:** Container-intern wird `host.docker.internal` statt `127.0.0.1`
für Ollama-URLs verwendet (siehe docker-compose.yml `extra_hosts`).

## Ollama Pool Mode (Load-Balanced Multi-Mac)

Ab Mai 2026 kann der Service **mehrere** Ollama-Endpoints parallel ansprechen
und Requests round-robin über sie verteilen. Genutzt z.B. um Macbook (über
VPS-Port 11434) und Mac mini (über VPS-Port 11435) als gemeinsamen Worker-Pool
laufen zu lassen.

### Aktivierung

In der `.env`:

```bash
# Single endpoint (Standard, legacy):
OLLAMA_URL=http://127.0.0.1:11434

# Pool mode — komma-separierte Liste, eine URL pro Backend-Mac:
OLLAMA_URLS=http://127.0.0.1:11434,http://127.0.0.1:11435
```

Ist `OLLAMA_URLS` leer/nicht gesetzt, fällt der Client auf `OLLAMA_URL`
zurück (1:1 wie vorher). Mit zwei oder mehr Endpoints wird Pool-Mode aktiv —
beim Service-Start erscheint im Log:

```
[INFO] providers.ollama: Ollama pool mode: 2 endpoints: ['http://127.0.0.1:11434', 'http://127.0.0.1:11435']
```

### Predictive Per-Model Routing

Verschiedene Macs haben oft nicht dieselben Modelle gepullt — kleinere Maschinen
können die großen 23-GB-Modelle gar nicht laden. Damit qwen3.6-Calls nicht
50% der Zeit auf der falschen Maschine landen, hält der Pool eine **Map** der
verfügbaren Modelle pro Endpoint:

- Beim ersten Request (und periodisch alle **5 Minuten**) wird `/api/tags`
  auf jedem Endpoint abgefragt und das Resultat gecacht.
- Bei einem Chat-Request werden bevorzugt Endpoints kontaktiert, die das
  angefragte Modell laut Map auch haben. Endpoints ohne das Modell werden
  als Last-Resort-Fallback angehängt (lässt eine stale Map selbst-heilen).
- Erhält ein Endpoint trotzdem mal 404 für ein Modell (z.B. weil das Modell
  zwischendurch entfernt wurde), wird der Map-Eintrag invalidiert und der
  nächste Endpoint versucht — fully self-healing.

Refresh-Status sieht man jederzeit im Service-Log:

```
[INFO] providers.ollama: Ollama model-map refreshed: 127.0.0.1:11434=9, 127.0.0.1:11435=8
```

### Failover-Verhalten

| Fall | Was passiert |
|---|---|
| Ein Endpoint nicht erreichbar (`ConnectionError`/`Timeout`) | Pool nimmt den nächsten in der RR-Reihenfolge |
| Endpoint antwortet mit 5xx | Pool retry'd nächsten (transient/load-related) |
| Endpoint antwortet mit 404 für das Modell | Pool retry'd nächsten + invalidiert Map-Eintrag |
| Endpoint antwortet mit 4xx (außer 404) | Bubbelt durch — deterministischer Client-Fehler |
| Alle Endpoints down | Wie vorher: Dispatcher fällt auf `fallback_provider` (z.B. Claude) zurück, oder queued in `request_queue` |

### Mac-Tunnels für Pool-Setup

Zweiter (und dritter, vierter…) Mac wird genauso angeschlossen wie der erste,
nur mit **anderem VPS-seitigen Port** im Reverse-Tunnel. Beispiel für den
Mac mini auf VPS-Port 11435:

```xml
<!-- ~/Library/LaunchAgents/com.ai-provider.ollama-tunnel.plist auf dem Mini -->
<string>-R</string>
<string>11435:127.0.0.1:11434</string>
```

VPS-side dann in `.env`:

```bash
OLLAMA_URLS=http://127.0.0.1:11434,http://127.0.0.1:11435
```

Anschließend `systemctl restart ai-provider-service`. Der Pool fragt beide
Endpoints beim nächsten Request automatisch nach ihren Modellen und routet
ab dann passend.

### Hardware-Anpassungen pro Mac

Kleinere Macs (z.B. M4 mini mit 24 GB unified memory) sollten reduzierte
`OLLAMA_*`-Werte bekommen, damit sie unter Drain-Last nicht swappen:

- `OLLAMA_NUM_PARALLEL`: **1** auf 24-GB-Maschinen (statt 4 auf 32-GB+),
  spart eine Slot-Wert KV-Cache.
- `OLLAMA_MAX_LOADED_MODELS`: **1** auf 24 GB (statt 2).
- Modell-Quant: auf 24 GB lieber **Q4_K_M** statt Q5_K_M — z.B. dev-coder
  basiert auf 24 GB vom `qwen2.5-coder:14b-instruct-q4_K_M` (9 GB),
  auf 32 GB+ vom q5 (10 GB). Schon ein GB Headroom entscheidet zwischen
  3-7s/Call und 60-90s/Call (swap-death).

## API-Übersicht

Alle Endpoints (außer `/health`) brauchen einen Bearer-Token. Unterstützt sind
`SERVICE_TOKEN`, `ADMIN_TOKEN` und ein admin-ausgestellter persönlicher
User-Token. Ein User-Token ist fest an genau eine `user_id` gebunden.

### Providers

```
GET /providers?user_id=<id>
  → Liste aller Provider mit configured/healthy/last_check
GET /providers/<id>/models?user_id=<id>
  → Live-Models vom Provider
POST /providers/<id>/test  { "user_id": "..." }
  → Verbindungs-Test (model count + sample)
GET /providers/<id>/health
  → aktueller Health-Status (gecacht)
```

### Configs

```
GET    /configs/<user_id>                     → alle Configs des Users
GET    /configs/<user_id>/<provider_id>       → eine Config (ohne API-Keys)
POST   /configs/<user_id>/<provider_id>       → erstellen/updaten
DELETE /configs/<user_id>/<provider_id>       → entfernen
```

POST-Body Beispiel (OpenAI):
```json
{
  "config": {
    "api_key": "sk-...",
    "organization_id": "org-..."
  },
  "fallback_provider": "claude",
  "queue_when_unavailable": false
}
```

Persönliche Keys werden für `claude`, `opencode`, `openai`, `zai` und
`ollama_cloud` unterstützt. Sie liegen Fernet-verschlüsselt in
`ProviderConfig` und werden nie zurückgegeben; Responses enthalten nur
`has_api_key`. Beispiel mit persönlichem User-Token:

```bash
curl -X POST https://<service>/configs/lisa/ollama_cloud \
  -H 'Authorization: Bearer aips_<one-time-token>' \
  -H 'Content-Type: application/json' \
  -d '{"config":{"api_key":"<OLLAMA_API_KEY>"}}'
```

Ollama Cloud ist ein eigener Provider und spricht `https://ollama.com/api`.
Er teilt weder Pool-, Tunnel- noch Health-Routing-Zustand mit lokalem `ollama`.

POST-Body Beispiel (Ollama mit Fallback auf Claude):
```json
{
  "config": {},
  "fallback_provider": "claude",
  "queue_when_unavailable": true,
  "queue_ttl_hours": 24
}
```

### Chat

```
POST /chat
  Body: {
    user_id, provider, model, messages, max_tokens,
    // optional Per-Request-Fallback (übersteuert DB-stored ProviderConfig.fallback_provider):
    fallback_provider?, fallback_model?, fallback_config?
  }
```

**Fallback-Quellen** (Priorität von hoch nach niedrig):
1. `fallback_provider` im Request-Body — pro Aufruf
2. `ProviderConfig.fallback_provider` in der DB — pro User+Provider gespeichert

Per-Request-Override erlaubt Clients, eine eigene Fallback-Strategie pro Aufruf
mitzugeben, ohne sie in der Service-DB zu persistieren. Nützlich, wenn der
Client (z.B. Bewerbungstracker) den Fallback-Provider in seiner eigenen User-DB
verwaltet (`user.ai_provider_backup`).

`fallback_config` ist ein optionales Dict (z.B. `{"api_key": "..."}`), das
einmalig statt der DB-Config verwendet wird — nützlich für Admin-User mit
Server-Key, wo nichts persistiert werden soll.

Antwort sync:
```json
{ "result": { "content": [{"text": "..."}], "usage": {...} },
  "via": "ollama", "fallback_used": false }
```

Antwort fallback:
```json
{ "result": {...}, "via": "claude",
  "fallback_used": true, "primary_provider": "ollama" }
```

Antwort queued (Ollama down + queue=on):
```json
{ "queued": true, "queue_id": "abc-123",
  "primary_provider": "ollama", "expires_at": "..." }
```


### OpenAI-compatible Endpoint (v1)

```
GET /v1/models
  → Dynamisch aus den aktuell verfügbaren Provider-Modellen generierte Liste
    im OpenAI-Format

 POST /v1/chat/completions
   Body (OpenAI-Format): {
     "model": "zai/glm-4.5",
     "messages": [{"role": "user", "content": "..."}],
     "stream": true,       // SSE streaming
     "max_tokens": 4096
   }
```

`/v1/models` fragt die für den authentifizierten User konfigurierten Provider
per `get_models()` ab. Nicht konfigurierte oder nicht erreichbare Provider
werden ausgelassen; lokale Ollama-Modelle erscheinen dadurch automatisch, sobald
sie auf einem Tunnel-Backend verfügbar sind (z.B. `ollama/ornith:latest`).

 Das Model-Format ist `provider/model_name`, z.B.:

 | Model-ID | Provider |
 |---|---|
 | `ollama/qwen3.6:latest` | Lokales Ollama |
 | `ollama/ornith:latest` | Lokales Ollama |
 | `zai/glm-4.5` | z.ai, wenn für den User konfiguriert und erreichbar |
 | `claude/claude-sonnet-4-6-20250514` | Claude, wenn für den User konfiguriert und erreichbar |

**Streaming:** `stream=true` liefert SSE (Server-Sent Events) — auch wenn der
Backend-Provider synchron aufgerufen wird, kommt die Antwort als ein Chunk.

**Auth:** Gleicher Bearer-Token wie `/chat` (`@require_token`).  
`@require_provider_access` ist **deaktiviert** — der Decorator sucht `provider` 
im JSON-Body, nicht im Model-Namen (`zai/glm-4-flash`). Für OpenAI-kompatible 
Clients reicht die Token-Authentisierung.

**Zweck:** Ermöglicht Pi und anderen OpenAI-kompatiblen Clients den Zugriff
auf den Service. Pi-Extension in `~/.pi/agent/extensions/ai-provider-service.ts`
registriert den Service automatisch.

Beispiel (non-streaming):
```bash
 curl -s https://<service>/v1/chat/completions   -H 'Authorization: Bearer <token>'   -H 'Content-Type: application/json'   -d '{"model":"zai/glm-4.5","messages":[{"role":"user","content":"Hallo"}],"stream":false}'
```

Beispiel (streaming):
```bash
 curl -sN https://<service>/v1/chat/completions   -H 'Authorization: Bearer <token>'   -H 'Content-Type: application/json'   -d '{"model":"zai/glm-4.5","messages":[{"role":"user","content":"Hallo"}],"stream":true}'
```

### Pi-Einrichtung

Damit Pi den Service als OpenAI-kompatiblen Provider nutzen kann:

1. **Extension** in `~/.pi/agent/extensions/ai-provider-service.ts` — registriert den
   Service per `pi.registerProvider()` mit `api: "openai-completions"`.
2. **Env-Variablen** in `~/.pi/agent/.env` setzen:
   ```bash
   SERVICE_TOKEN=<gleicher Token wie im ai-provider-service .env>
   AI_PROVIDER_SERVICE_URL=http://localhost:8767  # oder https://service.domain.de/ai-provider
   ```
 3. **Modelle** sind dann als `ai-provider-service/...` in Pi wählbar:
    - `--model ai-provider-service/zai/glm-4.5` — GLM-4.5
    - `--model ai-provider-service/zai/glm-4.5-air` — GLM-4.5 Air
    - `--model ai-provider-service/ollama/qwen3.6:latest` — Lokales Ollama
    - `--model ai-provider-service/claude/claude-sonnet-4-6-20250514` — Claude

**Hinweis:** Der Service läuft auf dem **oracle-vm** hinter Apache Reverse-Proxy.
Die URL ist `https://ai-provider-service.wolfinisoftware.de/`. Bei lokalem
Betrieb (`http://localhost:8767`) kann die Extension direkt verbinden.

### Queue

```
GET    /queue/<id>                              → Status + Result
GET    /queue?user_id=<id>&status=<s>           → Liste
DELETE /queue/<id>                              → cancel
```

## Integration aus Konsumenten-Apps

### Aus Bewerbungstracker (Python-Backend)

```python
import requests, os

SVC = 'http://127.0.0.1:8767'
TOKEN = os.getenv('AI_PROVIDER_TOKEN')

def chat(user_id, provider, model, messages, max_tokens=600,
         fallback_provider=None, fallback_model=None):
    body = {
        'user_id': user_id, 'provider': provider,
        'model': model, 'messages': messages, 'max_tokens': max_tokens,
    }
    # Optional: Per-Request-Fallback (übersteuert DB-Config)
    if fallback_provider:
        body['fallback_provider'] = fallback_provider
    if fallback_model:
        body['fallback_model'] = fallback_model
    r = requests.post(f'{SVC}/chat', json=body,
                      headers={'Authorization': f'Bearer {TOKEN}'}, timeout=120)
    r.raise_for_status()
    return r.json()
```

### Aus loganonymizer (Browser, JS)

Über Apache-Reverse-Proxy auf der gleichen Origin → kein CORS-Problem:

```javascript
const r = await fetch('/ai-provider/chat', {
    method: 'POST',
    headers: {
        'Authorization': `Bearer ${SERVICE_TOKEN}`,
        'Content-Type': 'application/json',
    },
    body: JSON.stringify({
        user_id: 'loganonymizer-default',
        provider: 'ollama',
        model: 'mistral',
        messages: [{role: 'user', content: '...'}],
    }),
});
const data = await r.json();
if (data.queued) {
    // Polling auf /ai-provider/queue/<id>
}
```

## MASTER_KEY-Rotation

Bei Kompromittierung des MASTER_KEY:

1) Alle Configs aus DB exportieren (vorher mit altem Key entschlüsseln)
2) Neuen MASTER_KEY in `.env` setzen
3) Configs mit neuem Key neu speichern (POST /configs/...)

Skript für 1) + 3) ist nicht enthalten — bei Bedarf manuell oder neuer
Migrations-Endpoint.

## Access control (provider gating)

The gateway gates server-funded non-`ollama` providers behind admin approval.
A personal key authorizes its owning user for that provider because that user
bears the cost. Authorization precedence is: gate kill switch, ungated provider,
admin, owning personal key, active grant.

- **ollama** — available to all callers (configurable via `UNGATED_PROVIDERS`)
- **claude, opencode, openai, zai, ollama_cloud** — an owning personal key
  bypasses approval; server-key use still follows grant/allowlist rules.
- **mammouth, custom** — require an active `ProviderGrant` or admin access.

A failed personal key never silently falls back to a server-funded key. Removing
the personal config restores the normal grant/allowlist behavior.

### Zentrale Provider-Keys (Server-Key-Allowlist)

Claude und z.ai können einen zentralen Server-Key nutzen, der per Allowlist
auf bestimmte `user_id`s beschränkt ist:

- **Claude** (`CLAUDE_SERVER_KEY_ALLOWED_USERS`) — leer = offen für alle
  (single-tenant Default).
- **z.ai** (`ZAI_SERVER_KEY_ALLOWED_USERS`) — leer = **nur `ADMIN_USER_ID`**
  darf den zentralen `ZAI_API_KEY` nutzen (Owner-only Default, inkl. der
  kostenlosen GLM-Flash-Modelle). Alle anderen User müssen einen eigenen
  z.ai-Key unter `/configs/<user_id>/zai` hinterlegen.

### z.ai (GLM) Tarif-Sync

Die GLM-Preise werden in `pricing.py` als statischer Snapshot gepflegt und
durch eine eigene Override-Datei `pricing_overrides_zai.json` ergänzt (getrennt
von `pricing_overrides.json`, damit der opencode-Cron sie nicht überschreibt):

- `flask update-zai-pricing` — lädt die z.ai-Preisseite
  (`docs.z.ai/guides/overview/pricing.md`), parst die Rate-Card, vergleicht sie
  mit dem letzten Snapshot, speichert sie und **mailt den Owner bei jeder
  Tarif-Änderung** (neue/entfernte Modelle, Preisänderungen).

Täglich ausführen (analog zum opencode-Pricing-Cron, 06:00 UTC), auf
oracle-vm via Host-Crontab gegen den Docker-Container:

```cron
0 6 * * * docker exec ai-provider flask update-zai-pricing >> /var/log/ai-provider-zai-pricing.log 2>&1
```

### Tokens

| Token | Role | `user_id` resolution |
|---|---|---|
| `ADMIN_TOKEN` | admin | always `Config.ADMIN_USER_ID` (env, default `harald`) |
| `SERVICE_TOKEN` | user | from request body/query (current behavior) |
| `aips_…` user token | user | fixed DB association; another `user_id` returns 403 |

### Admin UI

The admin UI is served at `/admin/ui/`.
- **via ai-admin.wolfinisoftware.de** — Apache Basic Auth + `X-Forwarded-User` auto-auth,
  no second login step needed.
- **direct access** — log in with `ADMIN_USER_ID`/`ADMIN_PASSWORD` or
  `ADMIN_TOKEN` via the form or URL (`/admin/ui?token=<ADMIN_TOKEN>`).
After login, the URL is `/admin/ui/users`.

Shows per-user roster with configured providers, active grants, and
30-day usage rollup. Approve/revoke buttons hit `/admin/grants` via the
session cookie. The user-detail page also issues, rotates, or revokes a
personal token. Its plaintext is shown exactly once.

### Personal settings UI

Users open `https://<service>/settings/login`, enter the one-time `aips_…`
token, and manage personal keys at `/settings/providers`. The signed session
stores only the user identity and token generation—not the token or provider
keys. Rotation/revocation invalidates existing settings sessions. Save, test,
remove, and logout actions are CSRF-protected; provider errors are sanitized.

### Admin REST API

`Authorization: Bearer <ADMIN_TOKEN>` on every endpoint.

```
POST   /admin/grants           {user_id, provider_id, note?}  → 201
GET    /admin/grants[?user_id=&provider_id=&include_revoked=true]
DELETE /admin/grants/<id>      → 204 (soft-delete)
POST   /admin/users/<user_id>/token   → 201 + plaintext token (shown once)
DELETE /admin/users/<user_id>/token   → 204 (also invalidates UI sessions)
GET    /admin/overview         → {users: [...]}
```

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

## Limitationen

- **Single-Instance:** SQLite-DB ist nicht für mehrere Service-Instanzen
  ausgelegt. Bei Last-Bedarf später auf PostgreSQL + Redis migrieren.
- **Health-Cache pro Worker:** bei mehreren Gunicorn-Workern hat jeder einen
  eigenen Health-Cache (akzeptabel — Stale-Detection ist konservativ).
- **Keine Cost-Tracking-API:** Cost-Logs bleiben in der Konsumenten-App
  (z.B. Bewerbungstracker `ApiCall`-Tabelle).
- **Anthropic-Models statisch:** kein offizielles `/models`-Endpoint —
  wird in `providers/claude.py:KNOWN_MODELS` gepflegt.
