# News-Agent systemd units

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
