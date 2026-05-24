# Open WebUI (self-hosted ChatGPT-Alternative)

Selbst-gehostete LLM-Oberfläche unter `https://chat.wolfinisoftware.de`.
Docker-Container an `127.0.0.1:3000` gebunden, Apache vorgeschaltet (eigener vhost,
Subdomain — Pfad-Prefix funktioniert wegen WebSockets/Asset-Pfaden nicht zuverlässig).

## Stack

Zwei Container, geteiltes compose-Netz:

```
Browser ──HTTPS──► Apache (chat.wolfinisoftware.de)
                      │
                      ▼
              open-webui (127.0.0.1:3000 → :8080 im Container)
                      │  OpenAI-Wire-Format, intern
                      ▼
              litellm   (nur internes Netz, Port 4000)
                      │  natives Anthropic / OpenAI / …
                      ▼
              Anthropic API   /   OpenAI API   /   …
```

LiteLLM ist notwendig, weil Open WebUI nativ nur OpenAI-kompatible APIs
spricht — es übersetzt für Anthropic Claude und gibt dir gleichzeitig eine
Stelle für Logging/Limits.

## Voraussetzungen

- Docker + docker-compose-plugin auf dem VPS (für SearXNG eh schon installiert).
- DNS-Eintrag `chat.wolfinisoftware.de` → VPS-IP.
- Apache-Module: `proxy`, `proxy_http`, `proxy_wstunnel`, `rewrite`, `ssl`.
  Prüfen: `httpd -M | grep -E 'proxy_wstunnel|rewrite|ssl'`.

## Deployment

```
ssh ionos-vps
sudo mkdir -p /opt/open-webui
sudo cp /var/www/ai-provider-service/deploy/open-webui/docker-compose.yml   /opt/open-webui/
sudo cp /var/www/ai-provider-service/deploy/open-webui/litellm-config.yaml  /opt/open-webui/
sudo cp /var/www/ai-provider-service/deploy/open-webui/.env.example         /opt/open-webui/.env
sudo chmod 600 /opt/open-webui/.env

# Secrets erzeugen
sudo sed -i "s/REPLACE_ME_SECRET_HEX_64/$(openssl rand -hex 32)/"            /opt/open-webui/.env
sudo sed -i "s/REPLACE_ME_LITELLM_MASTER_KEY/$(openssl rand -hex 32)/"       /opt/open-webui/.env

# .env mit echten Werten füllen: ANTHROPIC_API_KEY, ggf. OPENAI_API_KEY ...
sudo vi /opt/open-webui/.env

cd /opt/open-webui
sudo docker compose up -d

# Healthcheck: LiteLLM gibt die konfigurierten Modelle aus
sudo docker exec open-webui curl -sf -H "Authorization: Bearer $(grep ^LITELLM_MASTER_KEY /opt/open-webui/.env | cut -d= -f2)" http://litellm:4000/v1/models | head
```

Apache-vhost installieren und TLS holen:

```
sudo cp /var/www/ai-provider-service/deploy/open-webui/apache-vhost.conf \
        /etc/httpd/conf.d/open-webui.conf
sudo apachectl configtest
sudo systemctl reload httpd
sudo certbot --apache -d chat.wolfinisoftware.de
```

## Erster Login

- `https://chat.wolfinisoftware.de` öffnen.
- **Der erste registrierte Account wird automatisch Admin.** Sofort einloggen
  und Account anlegen, bevor jemand anderes draufkommt.
- `ENABLE_SIGNUP=false` in der Compose-Datei verhindert anschließende Selbst-
  Registrierungen. Weitere Nutzer legst du im Admin-Panel an.

## Updates

```
cd /opt/open-webui
sudo docker compose pull
sudo docker compose up -d
sudo docker image prune -f
```

Das `open-webui-data` Volume bleibt erhalten (Chat-Verlauf, Settings, RAG-DB).

## Backups

Alles Relevante liegt im Docker-Volume `open-webui_open-webui-data`.
Snapshot:

```
sudo docker run --rm \
    -v open-webui_open-webui-data:/data:ro \
    -v /var/backups:/backup \
    alpine tar czf /backup/open-webui-$(date +%F).tar.gz -C /data .
```

## Modelle pflegen

Die Modell-Liste steht in [`litellm-config.yaml`](./litellm-config.yaml).
Wenn Anthropic ein neues Modell veröffentlicht, einfach einen weiteren
`model_list`-Eintrag hinzufügen, dann:

```
cd /opt/open-webui
sudo docker compose restart litellm
```

Open WebUI zeigt die neuen Modelle automatisch im Dropdown (es ruft beim
Login `/v1/models` gegen LiteLLM auf).

Default-Modell pro Nutzer/Workspace: Open WebUI → *Settings → Models*.

## Kosten & Limits

LiteLLM kann pro Modell Budgets/Rate-Limits durchsetzen. Für den Anfang reicht
die Minimalkonfig in `litellm-config.yaml`. Wenn du das brauchst:
https://docs.litellm.ai/docs/proxy/cost_tracking — dafür ist allerdings eine
Postgres-DB nötig (separater Container).

## Notes

- An `127.0.0.1:3000` gebunden — keine direkte öffentliche Exposition.
- LiteLLM ist gar nicht auf dem Host gepublished (nur `expose: 4000` im
  compose-Netz). Damit ist der Anthropic-Key nicht über den VPS exponiert.
- WebSocket-Upgrade in Apache zwingend, sonst hängen Streaming-Antworten.
- `WEBUI_AUTH=true` + `ENABLE_SIGNUP=false`: einziger öffentlicher Pfad ist der
  Login. Trotzdem nur über HTTPS betreiben.
- Logs: `sudo docker logs open-webui -f` und `sudo docker logs litellm -f`.
- Volume-Pfad auf dem Host:
  `sudo docker volume inspect open-webui_open-webui-data -f '{{ .Mountpoint }}'`.

## Troubleshooting

- **„No models available" in der WebUI:** `docker logs litellm` — meist
  fehlender oder falscher `ANTHROPIC_API_KEY`, oder LiteLLM hat die config
  nicht eingelesen (Pfad-Bind in compose prüfen).
- **HTTP 400 von LiteLLM bei Anthropic-Calls:** `drop_params: true` muss in
  `litellm-config.yaml` gesetzt sein (Open WebUI sendet OpenAI-spezifische
  Felder wie `frequency_penalty`, die Anthropic nicht kennt).
- **Streaming hängt nach ein paar Wörtern:** Apache-Buffering. Sicherstellen
  dass `mod_proxy_wstunnel` aktiv ist und die `RewriteRule` für `Upgrade=websocket`
  greift (`a2query -m proxy_wstunnel`).
