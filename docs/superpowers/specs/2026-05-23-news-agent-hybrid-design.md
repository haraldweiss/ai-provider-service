# News-Agent: Hybrid Claude/Ollama mit Voll-Migrationspfad

**Datum:** 2026-05-23
**Status:** Draft — wartet auf User-Review
**Branch:** wird beim Implementation-Plan festgelegt

## Ziel

Den bestehenden Anthropic-Platform-Agenten `agent_013EWBvafL8FSkeo6tNKnAgS` (täglicher News-Roundup für das Local-LLM-Ökosystem mit WordPress-Output) in den `ai-provider-service` einbauen, sodass:

1. **Phase 1 (Hybrid):** Claude bleibt primär, Ollama (eigener Pool) ist Fallback
2. **Phase 2 (Voll-Migration):** mit einem `.env`-Switch komplett auf Ollama umstellbar, ohne Code-Änderung

Dabei wird das Tool-Calling im Service grundsätzlich nachgerüstet — der News-Agent ist der erste Konsument, weitere Tool-Agenten profitieren später.

## Nicht-Ziele (Out-of-Scope für diese Spec)

- Andere Anthropic-Agenten migrieren (separate Specs falls relevant)
- Featured Images, Bild-Generierung, automatische Übersetzung
- Multi-Site-Publishing (es gibt genau eine WordPress-Site)
- Dediziertes `cve_lookup`-Tool (Empfehlung für Phase 2, hier nur als Mitigation in der Error-Section erwähnt)
- Mid-Loop-Provider-Failover (siehe Architektur-Entscheidung unten)

## Architektur-Entscheidungen (mit Begründung)

1. **News-Agent als Modul `agents/news/` im ai-provider-service**, nicht als separates Deploy.
   *Begründung:* nutzt direkt `dispatch()`, Health-Tracking, Usage-Logging und Provider-Fallback ohne HTTP-Hop. Künftige Tool-Agenten landen analog unter `agents/`.

2. **Volle Tool-Calling-Integration in `BaseClient` + `dispatcher.dispatch()`**, statt nur News-Agent-lokaler Workaround.
   *Begründung:* die Provider-Schicht ist der richtige Ort für die Abstraktion. Ein nur-lokaler Workaround müsste später zurückgebaut werden.

3. **Dispatcher bleibt "one-shot": ein Call rein, eine Antwort raus** — der Runner orchestriert den Tool-Loop selbst.
   *Begründung:* folgt der Anthropic-/OpenAI-API-Semantik. Mid-Loop-Fallback Claude→Ollama ist konzeptuell unsauber (Tool-Call-IDs gehören dem Modell, das sie erzeugt hat) und macht `publish_to_wordpress`-Idempotenz zur Pflicht-Konstruktion mitten im Hot-Path. Fallback zwischen Cron-Läufen reicht für einen News-Crawler.

4. **SearXNG self-hosted auf dem VPS** (Docker), kein Drittanbieter.
   *Begründung:* User-Wahl. Kein API-Key-Management, keine Cloud-Kosten. Trade-Off: Setup-Aufwand + Verfügbarkeit ist unser Problem.

5. **WordPress: `news-agent` User mit Application Password, `status=publish`, Kategorie `AI-News`.**
   *Begründung:* User-Wahl direkt-live. Idempotenz im Tool verhindert Doubletten bei Retry.

6. **Default-Modell Ollama: `qwen3.6:latest`** (36B MoE, Qwen3.5-Familie), zusätzlich optional `qwen2.5:32b-instruct` für A/B.
   *Begründung:* einziges nicht-Coder-fokussiertes Generalist-MoE ≥30B im aktuellen Pool. Beste Tool-Calling-Eigenschaften unter den verfügbaren OSS-Modellen.

## Architektur-Übersicht

```
systemd-timer (täglich 07:00) ──► python -m agents.news.runner
                                       │
                                       ▼
            agents/news/runner.py
              • System-Prompt + Tool-Definitionen laden
              • Tool-Loop orchestrieren (ein dispatch()-Call pro Iteration)
                                       │
              ┌────────────────────────┴────────────────────────┐
              ▼                                                 ▼
       dispatcher.dispatch                            agents/news/tools.py
       (jetzt tools-fähig)                              • web_search   (→ SearXNG)
       Primary → Fallback                               • web_fetch    (→ Trafilatura)
       Health-Tracking                                  • publish_to_wordpress
       Usage-Logging
              │
              ├──► providers/claude.py  (native tools)
              └──► providers/ollama.py  (Ollama function-calling mapping)
                          │
                          ▼
              ┌─────────────────────┐
              │ SearXNG (VPS Docker)│
              │ wolfinisoftware.de  │
              │ Ollama-Pool         │
              └─────────────────────┘
```

## Komponenten

### 1. Provider-Schicht (Erweiterung)

**`providers/base.py`** — `create_message` bekommt einen `tools` Parameter:

```python
def create_message(
    self,
    model: str,
    messages: list[dict],
    max_tokens: int = 600,
    tools: list[dict] | None = None,
) -> dict:
    """
    Returns:
    {
      "content": [...],
      "stop_reason": "end_turn" | "tool_use",
      "tool_calls": [
        {"id": "...", "name": "web_search", "input": {...}},
        ...
      ],
      "usage": {...}
    }
    """
```

**Tool-Format** (provider-agnostisch, Claude-Schema als Lingua Franca):
```python
{
    "name": "web_search",
    "description": "...",
    "input_schema": {"type": "object", "properties": {...}, "required": [...]}
}
```

**`providers/claude.py`** — Tools werden 1:1 an `anthropic.Anthropic.messages.create(tools=...)` durchgereicht. Response-Mapping liest `ToolUseBlock` aus `response.content` und befüllt `tool_calls` + `stop_reason`.

**`providers/ollama.py`** — Tools werden ins OpenAI-kompatible Format unter `/api/chat` umgemappt:
```python
{"type": "function", "function": {"name": ..., "description": ..., "parameters": <input_schema>}}
```
Response: `data["message"]["tool_calls"]` zurück auf das Claude-Schema mappen. Wenn das Modell `tool_calls` zurückgibt, ist `stop_reason="tool_use"`, sonst `"end_turn"`.

### 2. Dispatcher (kleine Erweiterung)

`dispatcher.dispatch()` bekommt `tools=None` zusätzlich, reicht das durch zu `_execute()` und an den Client. Das bestehende Verhalten (Health, Fallback, UsageEvent-Logging) bleibt unverändert. `UsageEvent` zählt jeden LLM-Call separat — ein Tool-Loop-Lauf erzeugt also N Events mit `origin_app="news-agent"`.

### 3. News-Agent (`agents/news/`)

**`agents/news/runner.py`** — Tool-Loop:

```python
def run_news_agent(dry_run: bool = False) -> dict:
    messages = [{"role": "system", "content": NEWS_SYSTEM_PROMPT}]
    iteration = 0
    while iteration < MAX_ITERATIONS:
        result = dispatch(
            user_id="news-agent",
            provider_id=Config.NEWS_AGENT_PROVIDER,
            model=MODEL_FOR[Config.NEWS_AGENT_PROVIDER],
            messages=messages,
            tools=TOOLS,
            max_tokens=4096,
            fallback_provider_override=Config.NEWS_AGENT_FALLBACK or None,
            origin_app="news-agent",
        )
        msg = result["result"]
        messages.append({"role": "assistant", "content": msg["content"]})
        if msg["stop_reason"] != "tool_use":
            return _summarize_run(messages, iteration)
        tool_results = [
            {"type": "tool_result", "tool_use_id": c["id"],
             "content": execute_tool(c["name"], c["input"], dry_run=dry_run)}
            for c in msg["tool_calls"]
        ]
        messages.append({"role": "user", "content": tool_results})
        iteration += 1
    raise RuntimeError(f"Tool-Loop diverged after {MAX_ITERATIONS} iterations")
```

**`agents/news/tools.py`** — drei Tool-Funktionen:

- `web_search(query: str, max_results: int = 10) -> list[dict]`
  HTTP GET `${SEARXNG_URL}/search?q=...&format=json` → liefert `[{title, url, snippet}]`.

- `web_fetch(url: str) -> str`
  HTTP GET URL (10s Timeout, User-Agent gesetzt), Body durch `trafilatura.extract()` → sauberer Plain-Text. Cap auf ~20k Zeichen, sonst gekürzt mit Markierung.

- `publish_to_wordpress(title: str, body_html: str, tags: list[str]) -> dict`
  1. Idempotenz-Check: Beim ersten Aufruf eigene User-ID via `GET /wp-json/wp/v2/users/me` cachen. Dann `GET /wp-json/wp/v2/posts?author=<self-id>&after=<today-00:00>&search=<title>` — wenn Match existiert, dessen ID/URL zurückgeben und nicht erneut publishen.
  2. Tags-Slug-Lookup, fehlende Tags via `POST /wp-json/wp/v2/tags` anlegen.
  3. Kategorie-Lookup für `AI-News` (lazy first-run create).
  4. `POST /wp-json/wp/v2/posts` mit `status=Config.WORDPRESS_STATUS`, `categories=[id]`, `tags=[ids]`. Liefert `{post_id, url}` zurück.

**`agents/news/prompts.py`** — der Anthropic-Original-System-Prompt unverändert + angehängter Hinweis:
```
Schreibe den finalen WordPress-Post auf Deutsch. Section-Header sind bereits deutsch (🚀 Releases etc.).
```

**`agents/news/tool_schemas.py`** — `TOOLS = [...]`-Liste mit den drei JSON-Schemas.

### 4. Konfiguration (`config.py` + `.env`)

Neue `.env`-Variablen (alle additiv, keine Breaking-Changes):

```bash
NEWS_AGENT_PROVIDER=claude
NEWS_AGENT_FALLBACK=ollama
NEWS_AGENT_MODEL_CLAUDE=claude-sonnet-4-6
NEWS_AGENT_MODEL_OLLAMA=qwen3.6:latest
NEWS_AGENT_MAX_ITERATIONS=40

SEARXNG_URL=http://127.0.0.1:8888

WORDPRESS_URL=https://wolfinisoftware.de
WORDPRESS_USER=news-agent
WORDPRESS_APP_PASSWORD=HCdlq79FBMJH9gtib745g3K5
WORDPRESS_CATEGORY=AI-News
WORDPRESS_STATUS=publish
```

`config.py` liest die Werte und reicht sie an Runner + Tools.

**Hinweis Server-Key-Allowlist:** falls `CLAUDE_SERVER_KEY_ALLOWED_USERS` in der `.env` nicht leer ist (aktuell leer = offen), muss `news-agent` als `user_id` dort eingetragen werden — sonst lehnt `_is_claude_server_key_allowed()` den Anthropic-Aufruf ab. Aktuell ist die Allowlist deaktiviert; relevant nur falls in Zukunft Multi-Tenant aktiviert wird.

### 5. SearXNG (Infrastruktur)

Eigenes Verzeichnis `/opt/searxng/` auf dem VPS mit `docker-compose.yml`:

- Image: `docker.io/searxng/searxng:latest`
- Port: `127.0.0.1:8888:8080` (nur lokal, kein Public-Expose, kein Apache-vhost davor)
- `settings.yml`:
  - `search.formats: [json, html]`
  - `server.secret_key`: random 32-byte hex bei Setup generiert
  - `server.limiter: false`
  - Engines: google, duckduckgo, bing, github, reddit, hackernews aktiv; youtube/images aus
- Logging: json-file driver, rotation 10 MB × 3 Dateien

Start via `docker compose up -d` einmal beim Setup, systemd nicht nötig (Docker's `restart: unless-stopped` reicht).

### 6. systemd-Units

```ini
# /etc/systemd/system/news-agent.service
[Unit]
Description=AI News Agent (one-shot)
After=network-online.target
Requires=ai-provider-service.service

[Service]
Type=oneshot
User=ai-provider
WorkingDirectory=/opt/ai-provider-service
EnvironmentFile=/opt/ai-provider-service/.env
ExecStart=/opt/ai-provider-service/venv/bin/python -m agents.news.runner
StandardOutput=journal
StandardError=journal
```

```ini
# /etc/systemd/system/news-agent.timer
[Unit]
Description=AI News Agent daily run

[Timer]
OnCalendar=*-*-* 07:00:00 Europe/Berlin
Persistent=true

[Install]
WantedBy=timers.target
```

## Die drei Betriebsmodi (identische Codebase, nur `.env`-Switch)

| Modus | `NEWS_AGENT_PROVIDER` | `NEWS_AGENT_FALLBACK` | Verhalten |
|---|---|---|---|
| **Hybrid (Phase 1, Default)** | `claude` | `ollama` | Claude primär. Wenn Anthropic down → nächster Cron-Lauf transparent auf Ollama. |
| **A/B-Vergleich** | manuell variieren | leer | Heute Claude, morgen Ollama. Posts im Admin direkt vergleichbar. |
| **Voll-Migration (Phase 2)** | `ollama` | leer (oder `claude`) | Reines Ollama. Claude-Code-Pfad bleibt unverändert → jederzeit rückwärts kompatibel. |

## Datenfluss eines Laufs (Beispiel)

1. systemd timer feuert 07:00 → `news-agent.service` startet.
2. Runner lädt Prompt, baut initiales `messages`.
3. `dispatch()` mit `tools=TOOLS` → Claude antwortet mit `tool_calls=[web_search(query="Ollama release November 2026")]`.
4. Runner führt `web_search` → SearXNG-JSON → Liste von Hits.
5. Tool-Result zurück in `messages` → nächster `dispatch()`-Call.
6. Modell wählt 3–5 vielversprechende URLs → `web_fetch` parallel sequentiell.
7. Synthese + weitere Such-Rounds (für die anderen Sections im Prompt).
8. Modell ruft `publish_to_wordpress(title=..., body_html=..., tags=[...])`.
9. Tool-Result `{post_id, url}` zurück → Modell antwortet mit `stop_reason=end_turn`.
10. Runner loggt summary → exit 0.

Typische Iterationen pro Lauf: 15–35. `MAX_ITERATIONS=40` ist mit Puffer großzügig.

## Error-Handling

| Fehler | Verhalten |
|---|---|
| **SearXNG nicht erreichbar** | Tool-Result `{"error": "search backend unavailable"}` → Modell sieht Fehler, kann anderen Begriff probieren oder mit Hinweis im Post terminieren. Kein Lauf-Abbruch. |
| **`web_fetch` 404/5xx/Timeout** | Tool-Result `{"error": "fetch failed: <reason>", "url": "..."}` → Modell skipped die Quelle. |
| **`publish_to_wordpress` HTTP-Fehler** | Tool-Result mit Error → Modell kann Tags anpassen und nochmal probieren. Nach 3 Versuchen: Runner schreibt `/var/log/news-agent/failed-<timestamp>.md` mit Body + exit 1. |
| **Primary-Provider down** | `dispatch()`-Fallback greift beim ersten Call → ganzer Lauf auf Ollama. |
| **Beide Provider down** | `dispatch()` wirft `RuntimeError` → Runner loggt + exit 1. Kein Queueing. |
| **Tool-Loop divergiert** (>40 Iter) | RuntimeError, Bisherige Konversation nach `/var/log/news-agent/diverged-<ts>.md`, exit 1. |
| **Modell halluziniert CVE-Details** | Nicht code-level fangbar. Mitigation: System-Prompt verlangt explizit NVD/GitHub-Security-Advisories-Verifikation. Empfehlung Phase 2: dediziertes `cve_lookup`-Tool gegen NVD-API. |

## Tests

| File | Layer | Inhalt |
|---|---|---|
| `tests/test_provider_tools_claude.py` | Provider Unit | Gemockte Anthropic-Response mit `ToolUseBlock` → korrektes normalisiertes Schema |
| `tests/test_provider_tools_ollama.py` | Provider Unit | Gemocktes `/api/chat`-Response mit `tool_calls` → korrektes Mapping in beide Richtungen |
| `tests/test_dispatcher_tools.py` | Dispatcher | `dispatch(tools=...)` reicht durch, UsageEvent korrekt geloggt, Fallback funktioniert |
| `tests/test_news_runner.py` | Runner | Fake-Provider mit deterministischer Tool-Call-Sequenz → Loop konvergiert, Tools in richtiger Reihenfolge, MAX_ITERATIONS greift |
| `tests/test_news_tools.py` | Tools | SearXNG-JSON-Mock, Trafilatura-Extraktion, WP-REST-Idempotenz-Check, Tag/Category-Lazy-Create |

**Manueller E2E vor Production-Enable:** `python -m agents.news.runner --dry-run` führt einen vollen Lauf aus, aber `publish_to_wordpress` ist im Dry-Mode (gibt nur Payload zurück, postet nicht). Output als Markdown auf stdout.

## Logging & Observability

- **journalctl** (`journalctl -u news-agent.service`): pro Lauf ein strukturierter Eintrag — Startzeit, Provider, Modell, Anzahl Iterationen, Anzahl Tool-Calls je Typ, finale Post-ID, Dauer.
- **`UsageEvent`-Tabelle**: jeder LLM-Call ein Event mit `origin_app="news-agent"` → Cost-Aufschlüsselung per Use-Case via bestehendem `/usage/events`-Endpoint.
- **`failed-*.md` / `diverged-*.md`** in `/var/log/news-agent/`: forensisches Material bei Crash.

## Datei-Änderungen (Übersicht für den Implementation-Plan)

**Neu:**
- `agents/__init__.py`
- `agents/news/__init__.py`
- `agents/news/runner.py`
- `agents/news/tools.py`
- `agents/news/prompts.py`
- `agents/news/tool_schemas.py`
- `deploy/systemd/news-agent.service`
- `deploy/systemd/news-agent.timer`
- `deploy/searxng/docker-compose.yml`
- `deploy/searxng/settings.yml`
- 5 Test-Files (s. Tabelle)

**Geändert:**
- `providers/base.py` (`tools` Param)
- `providers/claude.py` (tools nativ + tool_use Response-Mapping)
- `providers/ollama.py` (tools mapping in beide Richtungen)
- `dispatcher.py` (`tools` durchreichen in `dispatch()` und `_execute()`)
- `config.py` (NEWS_AGENT_*, SEARXNG_URL, WORDPRESS_*)
- `.env.example` (alle neuen Variablen mit Kommentaren)
- `requirements.txt` (`trafilatura`)
- `README.md` (kurzer News-Agent-Abschnitt)

## Phase-1 → Phase-2 Migrationspfad

1. **Phase 1 (Hybrid) deployen** und ≥1 Woche laufen lassen. Output täglich kurz sichten (4 Wochen = 30 Posts, gut zum Modell-Bewerten).
2. **A/B-Woche:** `NEWS_AGENT_PROVIDER` täglich abwechseln, Posts vergleichen. Optional Tags `[via=claude]` / `[via=ollama]` setzen.
3. **Bei zufriedener Qualität:** `NEWS_AGENT_PROVIDER=ollama`, `NEWS_AGENT_FALLBACK=` (leer). Voll-Migration vollzogen.
4. **Phase 2-Erweiterungen** (separate Specs): `cve_lookup`-Tool, Mehrsprachigkeit, dedizierte Featured Images.
