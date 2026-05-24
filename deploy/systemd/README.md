# News-Agent systemd units

## Prerequisites (one-time, before enabling the timer)

1. **WordPress side** (run on VPS as admin):

       cd /var/www/wolfinisoftware
       sudo -u apache /usr/local/bin/wp term create category AI-News --porcelain
       sudo -u apache /usr/local/bin/wp term create post_tag Ollama --porcelain
       sudo -u apache /usr/local/bin/wp term create post_tag 'llama.cpp' --porcelain
       sudo -u apache /usr/local/bin/wp term create post_tag Open-Weight --porcelain
       sudo -u apache /usr/local/bin/wp term create post_tag Security --porcelain

   The `news-agent` user has role `author` and cannot create taxonomy terms — pre-creating is mandatory.

2. **`/var/www/ai-provider-service/.env`** must contain:
   - `ANTHROPIC_API_KEY=...` (Sonnet primary)
   - `WORDPRESS_USER`, `WORDPRESS_APP_PASSWORD`, `WORDPRESS_URL`
   - `NEWS_AGENT_*` and `SEARXNG_URL` (see `.env.example`)

3. **SearXNG** running as a podman container, bound to `127.0.0.1:8888`:

       sudo podman run -d --name searxng --restart=unless-stopped \
         -p 127.0.0.1:8888:8080 \
         -v /opt/searxng/settings.yml:/etc/searxng/settings.yml:ro \
         -e SEARXNG_BASE_URL=http://127.0.0.1:8888/ \
         docker.io/searxng/searxng:latest

## Install (on VPS)

```
sudo cp /var/www/ai-provider-service/deploy/systemd/news-agent.service /etc/systemd/system/
sudo cp /var/www/ai-provider-service/deploy/systemd/news-agent.timer /etc/systemd/system/
sudo mkdir -p /var/log/news-agent
sudo systemctl daemon-reload
sudo systemctl enable --now news-agent.timer
```

## Inspect

- Next run:    `systemctl list-timers news-agent.timer`
- Last run:    `sudo journalctl -u news-agent.service -n 200`
- Manual run:  `sudo systemctl start news-agent.service`
- Dry-run:     `sudo /var/www/ai-provider-service/venv/bin/python -m agents.news.runner --dry-run`

## Disable

```
sudo systemctl disable --now news-agent.timer
```
