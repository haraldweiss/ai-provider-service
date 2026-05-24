# ai-provider-service

Multi-Provider AI-Gateway mit Fallback-Routing und Queue-Persistenz für lokale Provider (Ollama).

**Eine Provider-Verwaltung für alle Apps.** Statt jeder Konsumenten-App
(Bewerbungstracker, loganonymizer, …) eigene API-Key-Verwaltung + CORS-Handling
zu geben, läuft dieser Service einmal zentral und alle Apps fragen ihn an.

## Features

- **5 Provider** out of the box: Claude, Ollama, OpenAI, Mammouth, Custom (OpenAI-kompatibel)
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

1) Code rüberkopieren:
```bash
scp -r . root@bewerbungen.wolfinisoftware.de:/var/www/ai-provider-service/
```

2) Setup-Script auf dem VPS:
```bash
ssh root@bewerbungen.wolfinisoftware.de
cd /var/www/ai-provider-service
bash deploy/setup-vps.sh
# → installiert requirements, systemd-Unit
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

## News-Agent (täglicher Local-LLM-News-Roundup)

Modul `agents/news/` — täglicher WordPress-Post mit News aus dem Local-LLM-Ökosystem
(Ollama, llama.cpp, drumherum). Läuft als systemd-Timer um 07:00 Europe/Berlin
und ist der erste Tool-Calling-Konsument des Service.

- **Provider-Switch (Hybrid → Voll-Migration):** `.env`-Variable `NEWS_AGENT_PROVIDER=claude|ollama`
- **Search-Backend:** SearXNG (Container auf VPS, `127.0.0.1:8888`)
- **Output:** WordPress REST API → `https://wolfinisoftware.de`
- **Spec:** [docs/superpowers/specs/2026-05-23-news-agent-hybrid-design.md](docs/superpowers/specs/2026-05-23-news-agent-hybrid-design.md)
- **Plan:** [docs/superpowers/plans/2026-05-23-news-agent-hybrid.md](docs/superpowers/plans/2026-05-23-news-agent-hybrid.md)

Manueller Lauf / Dry-Run:

    sudo systemctl start news-agent.service
    # oder mit Dry-Run (kein WP-Publish, Output als JSON):
    sudo /var/www/ai-provider-service/venv/bin/python -m agents.news.runner --dry-run

Logs: `sudo journalctl -u news-agent.service -n 200`.

**Wichtige Setup-Anforderungen (einmal pro Site):**
- Bot-User `news-agent` in WordPress mit Rolle `author` + Application-Password
- Kategorie `AI-News` und Tags (`Ollama`, `llama.cpp`, `Open-Weight`, `Security`) **vorab als Admin anlegen** — der `author`-User darf keine Kategorien/Tags erstellen, nur zuweisen. Per `wp-cli`: `wp term create category AI-News` / `wp term create post_tag <name>`.
- `ANTHROPIC_API_KEY` in `.env` (nicht nur in der systemd `Environment=`-Direktive), damit auch manuelle `python -m agents.news.runner`-Aufrufe funktionieren.

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

## Limitationen

- **Single-Instance:** SQLite-DB ist nicht für mehrere Service-Instanzen
  ausgelegt. Bei Last-Bedarf später auf PostgreSQL + Redis migrieren.
- **Health-Cache pro Worker:** bei mehreren Gunicorn-Workern hat jeder einen
  eigenen Health-Cache (akzeptabel — Stale-Detection ist konservativ).
- **Keine Cost-Tracking-API:** Cost-Logs bleiben in der Konsumenten-App
  (z.B. Bewerbungstracker `ApiCall`-Tabelle).
- **Anthropic-Models statisch:** kein offizielles `/models`-Endpoint —
  wird in `providers/claude.py:KNOWN_MODELS` gepflegt.
