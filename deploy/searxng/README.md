# SearXNG (news-agent search backend)

Self-hosted meta-search engine used by `agents/news/tools.py:web_search`.

## Deployment (VPS)

```
ssh ionos-vps
sudo mkdir -p /opt/searxng
sudo cp /opt/ai-provider-service/deploy/searxng/docker-compose.yml /opt/searxng/
sudo cp /opt/ai-provider-service/deploy/searxng/settings.yml.example /opt/searxng/settings.yml
sudo sed -i "s/REPLACE_ME_SECRET_HEX_64/$(openssl rand -hex 32)/" /opt/searxng/settings.yml
cd /opt/searxng
sudo docker compose up -d
```

Verify:

```
curl -s 'http://127.0.0.1:8888/search?q=ollama&format=json' | jq '.results | length'
```

Should return a number ≥ 1.

## Notes

- Only bound to `127.0.0.1:8888` — no public exposure, no Apache vhost.
- `server.limiter: false` because we are the only client.
- `settings.yml` itself is **gitignored** — only `settings.yml.example` is committed.
- Rotating the secret: `sudo sed -i "s/^  secret_key:.*/  secret_key: \"$(openssl rand -hex 32)\"/" /opt/searxng/settings.yml && sudo docker compose restart`
- Logs: `sudo docker logs searxng -f`.
