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

## Deployment (VPS + Mac-Tunnel)

### VPS

> ⚠️ **Niemals den `venv/`-Ordner auf den VPS kopieren / rsyncen.** Python-venvs
> sind nicht portabel: die Shebangs in `venv/bin/*` hardcoden den absoluten Pfad
> zum Mac-Interpreter (z.B. `/Users/haraldweiss/.../venv/bin/python3.14`), der
> auf dem Linux-VPS nicht existiert. Folge: gunicorn-Start scheitert mit
> `status=126` ("bad interpreter") und systemd restartet endlos (am 2026-05-26
> bis Restart-Counter 33000+ gelaufen, bevor's aufgefallen ist). Deshalb beim
> Deploy `--exclude='venv'` setzen und das venv **auf dem VPS** mit dem
> Linux-Python neu bauen — nicht mit dem mitgebrachten Mac-`python3.14`.

1) Code rüberkopieren (venv + caches ausschliessen!):
```bash
rsync -avz --delete \
    --exclude='venv' --exclude='__pycache__' --exclude='.git' --exclude='.env' \
    ./ root@bewerbungen.wolfinisoftware.de:/var/www/ai-provider-service/
```

2) Venv auf dem VPS bauen + Setup-Script:
```bash
ssh root@bewerbungen.wolfinisoftware.de
cd /var/www/ai-provider-service
/usr/bin/python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
bash deploy/setup-vps.sh
# → installiert systemd-Unit
# → enabled Service für Auto-Start beim Server-Neustart
```

3) `.env` auf dem VPS füllen:
   - `ANTHROPIC_API_KEY` (falls Claude verwendet)
   - `SERVICE_TOKEN` (für Client-Apps)
   - `ALLOWED_ORIGINS` (für CORS)

4) Apache-Config einfügen — Inhalt von `deploy/apache-vhost.conf` in den
   bestehenden vhost (z.B. `/etc/httpd/conf.d/bewerbungen.conf`) kopieren,
   dann `systemctl reload httpd`.

5) Service starten und überprüfen:
```bash
systemctl start ai-provider-service.service
systemctl status ai-provider-service.service
curl http://127.0.0.1:8767/health

# Service ist auto-enabled — startet automatisch nach Server-Neustart
systemctl is-enabled ai-provider-service.service
# → enabled
```

**Auto-Restart-Verhalten:** Die systemd-Unit ist konfiguriert mit:
- `Restart=always` — Service restartet bei Crash oder Server-Reboot
- `RestartSec=10` — 10 Sekunden Pause zwischen Restart-Versuchen
- `WantedBy=multi-user.target` — Auto-Start beim Boot

### Container (production, current)

The production deploy on the IONOS VPS runs the service as a podman-managed
container, driven by a Quadlet at
`/etc/containers/systemd/ai-provider.container` (versioned in
`deploy/ai-provider.container`).

**First-time setup on the VPS:**

```bash
# 1. Data dir — persistent SQLite DB + pricing overrides
mkdir -p /opt/ai-provider-data
chcon -Rt container_file_t /opt/ai-provider-data

# 2. Secrets file (never in git) — copy from existing .env
mkdir -p /etc/ai-provider && chmod 700 /etc/ai-provider
# Only secrets, not the non-secret defaults (those are in the Quadlet):
python3 -c "
import os
with open('/var/www/ai-provider-service/.env') as f:
    content = f.read()
lines = content.strip().split('\n')
skip_prefixes = ['HOST=', 'PORT=', 'GATE_ENABLED=', 'UNGATED_PROVIDERS=',
    'HEALTH_CHECK_INTERVAL_SEC=', 'QUEUE_DRAIN_INTERVAL_SEC=',
    'QUEUE_TTL_HOURS=', 'DATABASE_URL=']
filtered = [l for l in lines if not any(l.startswith(p) for p in skip_prefixes)]
with open('/etc/ai-provider/ai-provider.env', 'w') as f:
    f.write('\n'.join(filtered) + '\n')
"
chmod 600 /etc/ai-provider/ai-provider.env
```

**Important:** In the env file, set `OLLAMA_URL` / `OLLAMA_URLS` / `SEARXNG_URL`
to `host.containers.internal` instead of `127.0.0.1` because inside the
container, `127.0.0.1` refers to the container's own loopback, not the host.

```bash
# 3. Install the Quadlet
cp /var/www/ai-provider-service/deploy/ai-provider.container \
   /etc/containers/systemd/
systemctl daemon-reload

# 4. Build the image and start
cd /var/www/ai-provider-service
podman build -t localhost/ai-provider:latest .
systemctl start ai-provider.service
```

**Re-deploy after a code change:**

```bash
cd /var/www/ai-provider-service
git pull
podman build -t localhost/ai-provider:latest .
systemctl restart ai-provider.service
```

The container is dual-bound: `127.0.0.1:8767` (host services like
daily-roundup, bewerbungen) and `10.88.0.1:8767` (container consumers like
claudetracker). No consumer URL changes needed after migration.

### Mac (für Ollama-Tunnel)

Ollama läuft lokal auf dem Mac. Damit der VPS-Service Ollama erreicht, brauchen
wir einen persistenten Reverse-SSH-Tunnel.

1) autossh installieren:
```bash
brew install autossh
```

2) SSH-Key auf dem VPS hinterlegen (passwordless-Login):
```bash
ssh-copy-id root@bewerbungen.wolfinisoftware.de
```

3) LaunchAgent installieren:
```bash
cp deploy/com.ai-provider.ollama-tunnel.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ai-provider.ollama-tunnel.plist
```

4) Status prüfen:
```bash
launchctl list | grep ai-provider
# Auf dem VPS:
ssh root@bewerbungen.wolfinisoftware.de 'curl -s http://127.0.0.1:11434/api/tags'
```

Der Tunnel restarted sich automatisch bei Drop / Mac-Reboot. Wenn der Mac aus ist
oder schläft, ist Ollama nicht erreichbar — der Service queued Requests dann
automatisch und arbeitet sie ab, sobald der Mac (und damit der Tunnel) wieder da
ist.

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

Alle Endpoints (außer `/health`) brauchen `Authorization: Bearer <SERVICE_TOKEN>`.

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

The gateway gates non-`ollama` providers behind admin approval. Defaults:

- **ollama** — available to all callers (configurable via `UNGATED_PROVIDERS`)
- **claude, opencode, openai, mammouth, custom, zai** — require an active
  `ProviderGrant` row for the calling `user_id`, OR the caller must hold
  the `ADMIN_TOKEN`.

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

### Admin UI

Visit `https://<service>/admin/ui?token=<ADMIN_TOKEN>` once to bootstrap
a signed session cookie. After that, the URL is `/admin/ui/users`.

Shows per-user roster with configured providers, active grants, and
30-day usage rollup. Approve/revoke buttons hit `/admin/grants` via the
session cookie.

### Admin REST API

`Authorization: Bearer <ADMIN_TOKEN>` on every endpoint.

```
POST   /admin/grants           {user_id, provider_id, note?}  → 201
GET    /admin/grants[?user_id=&provider_id=&include_revoked=true]
DELETE /admin/grants/<id>      → 204 (soft-delete)
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
