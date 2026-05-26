# Open WebUI (self-hosted ChatGPT-Alternative)

Selbst-gehostete LLM-Oberfläche unter `https://chat.wolfinisoftware.de`.
Zwei Container, gemanaged via **Podman Quadlets** (systemd-native), an
`127.0.0.1:3000` gebunden, Apache vorgeschaltet (Subdomain — Pfad-Prefix
funktioniert wegen WebSockets/Asset-Pfaden nicht zuverlässig).

## Stack

```
Browser ──HTTPS──► Apache (chat.wolfinisoftware.de)
                      │
                      ▼
              open-webui  (127.0.0.1:3000 → :8080 im Container)
                      │
        ┌─────────────┼───────────────────────┐
        │ OpenAI-Wire │ Ollama-API            │
        │  intern     │ host.containers       │
        ▼             ▼                       │
   litellm        (host:11434)                │
   (interne ──┐    │                          │
    Bridge)   │    │ autossh-Tunnel           │
        │     │    ▼                          │
        │     ▼  Mac:11434 (Ollama)           │
   Anthropic postgres                         │
     API     (spend-tracking)
```

- **Ollama** ist der Default-Provider — Modelle laufen auf dem Mac, via
  autossh-Reverse-Tunnel ([com.ai-provider.ollama-tunnel.plist](../com.ai-provider.ollama-tunnel.plist))
  als `127.0.0.1:11434` auf dem VPS. Der open-webui-Container erreicht das
  über `host.containers.internal` (Podman-Builtin).
- **Anthropic Claude** als Zweitprovider, durch den LiteLLM-Proxy
  OpenAI-kompatibel gemacht. LiteLLM ist nur im Quadlet-Netz erreichbar,
  der Anthropic-Key bleibt im Container.
- Default-Modell für neue Chats: `gemma4` (`hf.co/Jiunsong/supergemma4-...`),
  konfiguriert via `DEFAULT_MODELS` im [open-webui.container](./open-webui.container).
  Ändern: Wert anpassen → `daemon-reload` → `restart open-webui.service`.

## Voraussetzungen

- Podman ≥ 4.4 (für Quadlet-Support).
- Apache-Module: `proxy`, `proxy_http`, `proxy_wstunnel`, `rewrite`, `ssl`.
- DNS-Eintrag `chat.wolfinisoftware.de` → VPS-IP.
- Autossh-Tunnel vom Mac (`com.ai-provider.ollama-tunnel.plist`) aktiv,
  damit Ollama auf VPS-`127.0.0.1:11434` erreichbar ist — sonst zeigt das
  Modell-Dropdown nur die Claude-Optionen.

## Deployment

```
ssh ionos-vps
cd /var/www/ai-provider-service && sudo git pull

# Daten- und Config-Verzeichnisse
sudo mkdir -p /opt/open-webui/data
sudo mkdir -p /opt/litellm-db/data
sudo cp deploy/open-webui/litellm-config.yaml /opt/open-webui/
sudo cp deploy/open-webui/.env.example        /opt/open-webui/.env
sudo chmod 600 /opt/open-webui/.env

# Secrets erzeugen — LITELLM_MASTER_KEY == OPENAI_API_KEY für Open WebUI!
KEY="sk-$(openssl rand -hex 32)"
PG="$(openssl rand -hex 24)"
sudo sed -i "s/REPLACE_ME_SECRET_HEX_64/$(openssl rand -hex 32)/"  /opt/open-webui/.env
sudo sed -i "s|REPLACE_ME_MASTER_KEY|$KEY|g"                       /opt/open-webui/.env
sudo sed -i "s|REPLACE_ME_POSTGRES_PASSWORD|$PG|g"                 /opt/open-webui/.env

# ANTHROPIC_API_KEY eintragen (z.B. aus /var/www/ai-provider-service/.env übernehmen)
sudo vi /opt/open-webui/.env

# Quadlets installieren
sudo cp deploy/open-webui/open-webui.network    /etc/containers/systemd/
sudo cp deploy/open-webui/postgres.container    /etc/containers/systemd/
sudo cp deploy/open-webui/litellm.container     /etc/containers/systemd/
sudo cp deploy/open-webui/open-webui.container  /etc/containers/systemd/

sudo systemctl daemon-reload
sudo systemctl start postgres.service     # zuerst — litellm requires=postgres
sudo systemctl start litellm.service
sudo systemctl start open-webui.service

# Logs / Status
sudo systemctl status litellm.service open-webui.service
sudo journalctl -u litellm.service -f
```

Apache vhost installieren und TLS holen:

```
sudo cp deploy/open-webui/apache-vhost.conf /etc/httpd/conf.d/open-webui.conf
sudo apachectl configtest
sudo systemctl reload httpd
sudo certbot --apache -d chat.wolfinisoftware.de
```

Ollama-Guard (Fail-fast-Proxy vor dem Reverse-Tunnel) installieren — siehe
[Resilienz](#resilienz) für Details:

```
sudo cp deploy/open-webui/ollama-guard.conf /etc/httpd/conf.d/ollama-guard.conf
sudo semanage port -a -t http_port_t -p tcp 11436    # SELinux: neuer Apache-Port
sudo systemctl restart httpd                          # neue Listen-Direktive braucht restart, kein reload
```

Auto-Trim-Timer installieren (siehe [Resilienz](#resilienz)):

```
sudo install -d -m 755 /opt/open-webui/ops
sudo install -m 755 deploy/open-webui/ops/trim_empty_chats.py     /opt/open-webui/ops/
sudo install -m 644 deploy/open-webui/ops/open-webui-trim.service /etc/systemd/system/
sudo install -m 644 deploy/open-webui/ops/open-webui-trim.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now open-webui-trim.timer
```

## Erster Login

- `https://chat.wolfinisoftware.de` öffnen.
- **Der erste registrierte Account wird automatisch Admin.** Sofort selbst
  einloggen und Account anlegen, bevor jemand anderes draufkommt.
- `ENABLE_SIGNUP=false` in den Quadlets verhindert anschließende Selbst-
  Registrierungen. Weitere Nutzer legst du im Admin-Panel an.

## Modelle pflegen

Modell-Liste steht in [`litellm-config.yaml`](./litellm-config.yaml). Neue
Modelle ergänzen, dann:

```
sudo cp /var/www/ai-provider-service/deploy/open-webui/litellm-config.yaml /opt/open-webui/
sudo systemctl restart litellm.service
```

Default-Modell pro Nutzer/Workspace: Open WebUI → *Settings → Models*.

## Spend-Tracking

LiteLLM persistiert mit dem Postgres-Backend alle Calls (Tokens + Cost):

```bash
MASTER=$(sudo grep ^LITELLM_MASTER_KEY /opt/open-webui/.env | cut -d= -f2-)

# Globale Spend-Summe seit Start
sudo podman exec open-webui curl -sf \
  -H "Authorization: Bearer $MASTER" \
  http://litellm:4000/global/spend | jq

# Heutige Calls pro Modell
sudo podman exec open-webui curl -sf \
  -H "Authorization: Bearer $MASTER" \
  "http://litellm:4000/spend/logs?start_date=$(date +%F)&end_date=$(date +%F)" \
  | jq '.[] | {model, total_tokens, spend}'
```

Virtuelle Keys mit Per-User-Budget + Rate-Limit:
https://docs.litellm.ai/docs/proxy/virtual_keys

## Updates

```
cd /var/www/ai-provider-service && sudo git pull
sudo cp deploy/open-webui/*.container /etc/containers/systemd/
sudo cp deploy/open-webui/*.network   /etc/containers/systemd/
sudo cp deploy/open-webui/litellm-config.yaml /opt/open-webui/
sudo systemctl daemon-reload
sudo systemctl restart litellm.service open-webui.service
```

Container-Images regelmäßig aktualisieren (Quadlets pullen nicht automatisch):

```
sudo podman pull ghcr.io/open-webui/open-webui:main
sudo podman pull ghcr.io/berriai/litellm:main-stable
sudo systemctl restart litellm.service open-webui.service
```

## Backups

Alles Relevante liegt in `/opt/open-webui/data`. Snapshot:

```
sudo tar czf /var/backups/open-webui-$(date +%F).tar.gz -C /opt/open-webui data
```

## Resilienz

Zwei Schichten gegen kaputten Chatverlauf bei Tunnel-Aussetzer (Mac-Reboot,
Netzhickser, Ollama hängt):

1. **`ollama-guard.conf`** — Apache-vhost auf `10.89.0.1:11436` (Bridge-IP des
   Container-Netzwerks, extern nicht erreichbar) proxied zu `127.0.0.1:11434`
   mit `connectiontimeout=3 retry=0`. OpenWebUI redet via
   `OLLAMA_BASE_URL=http://host.containers.internal:11436` nur noch durch den
   Guard. Backend down → 503 in ~1 ms statt minutenlangem Hang, dadurch
   entstehen seltener leere Assistant-Knoten in der Chat-Historie.
2. **`open-webui-trim.timer`** — feuert alle 5 Minuten
   [`ops/trim_empty_chats.py`](./ops/trim_empty_chats.py): walked von
   `history.currentId` rückwärts, entfernt trailing-empty Leaves älter als 60 s
   (kein Race mit Live-Streams), korrigiert `currentId` und rebuildet die flat
   `messages`-Liste. Idempotent — wenn nichts zu trimmen ist, kein Schreibzugriff.

Manuell anstoßen / Status:

```
sudo systemctl start open-webui-trim.service           # einmal jetzt laufen
sudo systemctl list-timers open-webui-trim.timer
sudo journalctl -u open-webui-trim.service -n 20
sudo python3 /opt/open-webui/ops/trim_empty_chats.py \
    --db /opt/open-webui/data/webui.db --dry-run --verbose
```

## Notes

- An `127.0.0.1:3000` gebunden — keine direkte öffentliche Exposition.
- LiteLLM ist nirgends auf dem Host gepublished (nur im `open-webui`-Netz).
- WebSocket-Upgrade in Apache zwingend, sonst hängen Streaming-Antworten.
- Logs: `sudo journalctl -u open-webui.service -f`,
  `sudo journalctl -u litellm.service -f`.

## Troubleshooting

- **„No models available" in der WebUI:** `journalctl -u litellm.service` —
  meist fehlender/falscher `ANTHROPIC_API_KEY` in `/opt/open-webui/.env`.
- **HTTP 401 von Open WebUI nach LiteLLM:** `LITELLM_MASTER_KEY` und
  `OPENAI_API_KEY` in `.env` sind **nicht identisch**.
- **HTTP 400 von LiteLLM bei Anthropic-Calls:** `drop_params: true` muss in
  `litellm-config.yaml` gesetzt sein.
- **Streaming hängt nach ein paar Wörtern:** Apache-Buffering. Sicherstellen
  dass `mod_proxy_wstunnel` aktiv ist und die `RewriteRule` für
  `Upgrade=websocket` greift (`httpd -M | grep wstunnel`).
- **systemctl: Unit not found:** nach Quadlet-Änderungen IMMER
  `sudo systemctl daemon-reload`.
