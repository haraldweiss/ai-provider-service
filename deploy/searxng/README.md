# SearXNG (meta-search backend)

Self-hosted meta-search, gemanaged via Podman Quadlet. Genutzt von:

- **news-agent** (`agents/news/tools.py:web_search`) auf dem VPS-Host über
  `http://127.0.0.1:8888/`.
- **Open WebUI**'s Websuche aus dem Container heraus über
  `http://searxng:8080/` (gemeinsames `open-webui`-Quadlet-Netz).

## Deployment (Erstinstallation)

```
ssh ionos-vps
cd /var/www/ai-provider-service && sudo git pull

# Settings-Datei + Secret
sudo mkdir -p /opt/searxng
sudo cp deploy/searxng/settings.yml.example /opt/searxng/settings.yml
sudo sed -i "s/REPLACE_ME_SECRET_HEX_64/$(openssl rand -hex 32)/" /opt/searxng/settings.yml

# Quadlet installieren
sudo cp deploy/searxng/searxng.container /etc/containers/systemd/
sudo systemctl daemon-reload
sudo systemctl start searxng.service
```

Verify (aus dem VPS-Host):
```
curl -s 'http://127.0.0.1:8888/search?q=ollama&format=json' | jq '.results | length'
```

Sollte ≥ 1 sein.

Verify aus dem open-webui-Container:
```
sudo podman exec open-webui curl -sf 'http://searxng:8080/search?q=test&format=json' | jq '.results | length'
```

## Migration von manuell gestartetem podman-Container

Falls noch ein per `podman run` gestarteter searxng-Container läuft:

```
sudo podman stop searxng
sudo podman rm searxng
sudo cp deploy/searxng/searxng.container /etc/containers/systemd/
sudo systemctl daemon-reload
sudo systemctl start searxng.service
```

## Update / Image-Refresh

```
sudo podman pull docker.io/searxng/searxng:latest
sudo systemctl restart searxng.service
```

## Notes

- Nur an `127.0.0.1:8888` gebunden — keine direkte öffentliche Exposition.
- `server.limiter: false` weil wir intern die einzigen Clients sind.
- `settings.yml` ist **gitignored** — nur `settings.yml.example` ist committet.
- Secret rotieren:
  `sudo sed -i "s/^  secret_key:.*/  secret_key: \"$(openssl rand -hex 32)\"/" /opt/searxng/settings.yml && sudo systemctl restart searxng.service`
- Logs: `sudo journalctl -u searxng.service -f`.
- Network: hängt im `open-webui.network` (gemeinsam mit open-webui + litellm),
  damit der WebUI-Container searxng per DNS-Name auflösen kann.
