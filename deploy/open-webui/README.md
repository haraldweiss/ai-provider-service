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
                      │   OpenAI-Wire-Format, intern via Bridge-Netz
                      ▼
              litellm     (nur internes Netz "open-webui", Port 4000)
                      │   natives Anthropic
                      ▼
              Anthropic API
```

Beide Container hängen im gemeinsamen Quadlet-Netz `open-webui`; LiteLLM ist
auf dem Host gar nicht publiziert. Damit ist der `ANTHROPIC_API_KEY` nirgends
über die VPS-Netzschnittstelle exponiert.

## Voraussetzungen

- Podman ≥ 4.4 (für Quadlet-Support).
- Apache-Module: `proxy`, `proxy_http`, `proxy_wstunnel`, `rewrite`, `ssl`.
- DNS-Eintrag `chat.wolfinisoftware.de` → VPS-IP.

## Deployment

```
ssh ionos-vps
cd /var/www/ai-provider-service && sudo git pull

# Daten- und Config-Verzeichnis
sudo mkdir -p /opt/open-webui/data
sudo cp deploy/open-webui/litellm-config.yaml /opt/open-webui/
sudo cp deploy/open-webui/.env.example        /opt/open-webui/.env
sudo chmod 600 /opt/open-webui/.env

# Secrets erzeugen — LITELLM_MASTER_KEY == OPENAI_API_KEY für Open WebUI!
KEY="sk-$(openssl rand -hex 32)"
sudo sed -i "s/REPLACE_ME_SECRET_HEX_64/$(openssl rand -hex 32)/"  /opt/open-webui/.env
sudo sed -i "s|REPLACE_ME_MASTER_KEY|$KEY|g"                       /opt/open-webui/.env

# ANTHROPIC_API_KEY eintragen (z.B. aus /var/www/ai-provider-service/.env übernehmen)
sudo vi /opt/open-webui/.env

# Quadlets installieren
sudo cp deploy/open-webui/open-webui.network    /etc/containers/systemd/
sudo cp deploy/open-webui/litellm.container     /etc/containers/systemd/
sudo cp deploy/open-webui/open-webui.container  /etc/containers/systemd/

sudo systemctl daemon-reload
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
