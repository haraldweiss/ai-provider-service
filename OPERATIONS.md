# Operations Handbook

Complete guide for running, monitoring, and troubleshooting the centralized AI provider service in production.

---

## Quick Reference

**Service Name:** `ai-provider-service.service`

**Status Commands:**
```bash
# Check current status
systemctl status ai-provider-service.service

# View recent logs
journalctl -u ai-provider-service.service -n 50 --no-pager

# Follow logs in real-time
journalctl -u ai-provider-service.service -f

# Check if enabled for auto-boot
systemctl is-enabled ai-provider-service.service
```

**Common Operations:**
```bash
# Start service
systemctl start ai-provider-service.service

# Stop service
systemctl stop ai-provider-service.service

# Restart service
systemctl restart ai-provider-service.service

# Reload configuration (if changed)
systemctl daemon-reload
systemctl restart ai-provider-service.service

# Enable auto-start on boot
systemctl enable ai-provider-service.service

# Disable auto-start (service still runs if started manually)
systemctl disable ai-provider-service.service
```

---

## Service Configuration

### Systemd Unit File Location
```
/etc/systemd/system/ai-provider-service.service
```

### Environment Variables
The service reads from `/var/www/ai-provider-service/.env`:

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `HOST` | Yes | `0.0.0.0` | Interface to bind (0.0.0.0 = all interfaces) |
| `PORT` | Yes | `8767` | Port number |
| `STARTUP_MODE` | No | `lazy` | `lazy` = load models on-demand; `eager` = load all on startup |
| `SERVICE_TOKEN` | Yes | - | Bearer token for client authentication |
| `ANTHROPIC_API_KEY` | Conditional | - | Required if using Claude provider |
| `MASTER_KEY` | Yes | - | Fernet key for encrypting user API keys in database |
| `ALLOWED_ORIGINS` | No | `*` | CORS whitelist (comma-separated or `*`) |
| `DATABASE_URL` | No | `sqlite:///ai_provider.db` | Database connection string |
| `OLLAMA_URL` | No | `http://127.0.0.1:11434` | Single-endpoint Ollama URL (legacy mode) |
| `OLLAMA_URLS` | No | (empty) | Comma-separated list of Ollama endpoints — activates **Pool Mode** (load-balanced multi-Mac). When set, overrides `OLLAMA_URL`. See README → "Ollama Pool Mode" for details. |

### Auto-Restart Behavior

**Enabled:** Yes
- **Restart Policy:** `Restart=always`
- **Restart Delay:** 10 seconds between restart attempts
- **Boot Behavior:** Service starts automatically on server reboot
- **Crash Recovery:** Service restarts immediately if it crashes

---

## Health Checks

### Manual Health Check
```bash
# Local check
curl http://127.0.0.1:8767/health

# Remote check (if accessible)
curl http://<server-ip>:8767/health

# With authentication
curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://<server-ip>:8767/health
```

**Expected Response:**
```json
{"status": "ok"}
```

### Provider Status
```bash
curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://<server-ip>:8767/providers
```

Returns health status of all configured providers (Claude, Ollama, OpenAI, etc.).

### Model Status
```bash
curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://<server-ip>:8767/models/status
```

Returns:
- Loaded models
- VRAM usage percentage
- Hardware specifications (GPU VRAM, System RAM, CPU cores)

---

## Monitoring

### System Resource Usage
```bash
# Check service memory and CPU
systemctl status ai-provider-service.service | grep -E "Memory|CPU"

# Monitor in real-time
watch -n 2 'systemctl status ai-provider-service.service | grep -E "Memory|CPU|Active"'

# Get detailed process info
ps aux | grep "[p]ython.*app.py"
```

### Log Monitoring

**View last 50 lines:**
```bash
journalctl -u ai-provider-service.service -n 50 --no-pager
```

**View logs from specific time:**
```bash
# Since 10 minutes ago
journalctl -u ai-provider-service.service --since "10 minutes ago"

# Specific date range
journalctl -u ai-provider-service.service --since "2026-05-13 08:00" --until "2026-05-13 09:00"
```

**Filter logs by level:**
```bash
# Errors only
journalctl -u ai-provider-service.service -p err

# Warnings and above
journalctl -u ai-provider-service.service -p warning
```

**Real-time monitoring:**
```bash
# Follow logs with timestamps
journalctl -u ai-provider-service.service -f --output=short-precise

# Or with colors
journalctl -u ai-provider-service.service -f -o cat | less +F
```

**Ollama pool-specific logs (when `OLLAMA_URLS` is set):**
```bash
# Current routing table (per-endpoint model counts)
journalctl -u ai-provider-service.service --since "10 minutes ago" \
  | grep "model-map refreshed"
# → e.g. "model-map refreshed: 127.0.0.1:11434=9, 127.0.0.1:11435=8"

# Failover events (endpoint unreachable, 5xx, or 404 → retry next)
journalctl -u ai-provider-service.service --since "1 hour ago" \
  | grep -E "trying next|unreachable|returned 404"

# Pool init line (on service start)
journalctl -u ai-provider-service.service -b \
  | grep "Ollama pool mode"
# → "Ollama pool mode: 2 endpoints: ['http://127.0.0.1:11434', 'http://127.0.0.1:11435']"
```

If you suddenly see lots of `trying next`-events: one of the endpoints is
sick (or the SSH tunnel to it broke). Check the corresponding Mac's
`ollama.log` for swap / OOM / cold-start symptoms.

### Performance Metrics
Monitor these metrics regularly:

1. **Response Time**
   ```bash
   curl -w "\nTime: %{time_total}s\n" \
     -H "Authorization: Bearer $SERVICE_TOKEN" \
     http://127.0.0.1:8767/models/status
   ```

2. **VRAM Usage**
   ```bash
   curl -s -H "Authorization: Bearer $SERVICE_TOKEN" \
     http://127.0.0.1:8767/models/status | jq '.utilization_pct'
   ```

3. **Loaded Models**
   ```bash
   curl -s -H "Authorization: Bearer $SERVICE_TOKEN" \
     http://127.0.0.1:8767/models/status | jq '.loaded'
   ```

4. **Process Count**
   ```bash
   ps aux | grep "[p]ython.*app.py" | wc -l
   ```

---

## Troubleshooting

### Service Won't Start

**Symptom:** `systemctl status ai-provider-service.service` shows failed

**Check logs:**
```bash
journalctl -u ai-provider-service.service -n 30
```

**Common causes:**

1. **Port Already in Use**
   ```bash
   # Check what's using port 8767
   lsof -i :8767
   
   # Kill existing process if needed
   pkill -9 -f "python.*app.py"
   
   # Then restart
   systemctl restart ai-provider-service.service
   ```

2. **Missing or Invalid .env**
   ```bash
   # Check .env exists
   cat /var/www/ai-provider-service/.env
   
   # Verify required vars are set
   grep -E "SERVICE_TOKEN|ANTHROPIC_API_KEY|MASTER_KEY" .env
   ```

3. **Permission Issues**
   ```bash
   # Check file permissions
   ls -la /var/www/ai-provider-service/
   
   # Should be readable by root (service runs as root)
   # Fix if needed:
   chown -R root:root /var/www/ai-provider-service
   chmod 755 /var/www/ai-provider-service
   chmod 600 /var/www/ai-provider-service/.env
   ```

### Service Crashes Frequently

**Check restart count:**
```bash
journalctl -u ai-provider-service.service | grep -c "Started"
```

**Common causes:**

1. **Out of Memory**
   ```bash
   # Check available memory
   free -h
   
   # Check if service is being killed by OOM
   journalctl -b | grep -i "killed.*ai-provider\|out of memory"
   
   # Solution: reduce VRAM usage by unloading models
   curl -X POST -H "Authorization: Bearer $SERVICE_TOKEN" \
     -d '{"model_name": "large-model"}' \
     http://127.0.0.1:8767/models/unload
   ```

2. **Database Locked**
   ```bash
   # Check for database locks
   ls -la /var/www/ai-provider-service/ai_provider.db*
   
   # If .wal or .shm files exist, check for competing access
   # Solution: ensure only one service instance is running
   pkill -f "python.*app.py"
   systemctl restart ai-provider-service.service
   ```

3. **Authentication or Network Errors**
   ```bash
   # Check if external providers are accessible
   curl https://api.anthropic.com/health 2>&1 | head -5
   
   # Check ANTHROPIC_API_KEY is valid
   curl https://api.anthropic.com/v1/models \
     -H "anthropic-version: 2023-06-01" \
     -H "x-api-key: $ANTHROPIC_API_KEY" 2>&1 | head -20
   ```

### Service Runs But Returns Errors

**Test basic connectivity:**
```bash
curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://127.0.0.1:8767/health

curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://127.0.0.1:8767/models/status
```

**Common errors:**

| Error | Cause | Solution |
|-------|-------|----------|
| 401 Unauthorized | Wrong or missing SERVICE_TOKEN | Verify `SERVICE_TOKEN` in .env matches request header |
| 502 Bad Gateway | Provider unreachable | Check provider status: `curl .../providers` |
| 503 Service Unavailable | Server overloaded or VRAM exhausted | Check `/models/status` VRAM usage; unload models |
| Connection refused | Service not running | `systemctl start ai-provider-service.service` |
| Timeout | Service hanging | Check logs; restart if necessary: `systemctl restart ai-provider-service.service` |

### Client Integration Issues

**Verify from client machine:**
```bash
# Test connectivity
curl http://82.165.185.108:8767/health

# Test auth
curl -H "Authorization: Bearer test-token" \
  http://82.165.185.108:8767/models/status

# Test chat endpoint
curl -X POST \
  -H "Authorization: Bearer test-token" \
  -H "Content-Type: application/json" \
  -d '{"provider":"claude","model":"claude-3-5-haiku-20241022","messages":[{"role":"user","content":"hi"}],"max_tokens":20}' \
  http://82.165.185.108:8767/chat
```

**Run integration verification script:**
```bash
# On client repo
cd /path/to/client-repo
AI_PROVIDER_SERVICE_URL=http://82.165.185.108:8767 \
AI_PROVIDER_SERVICE_TOKEN=test-token \
python verify_integration.py
```

---

## Maintenance Tasks

### Regular Checks (Daily)

```bash
#!/bin/bash
# Run this daily to verify service health

SERVICE="ai-provider-service.service"

echo "=== Service Status ==="
systemctl status $SERVICE | grep -E "Active|Status"

echo -e "\n=== Recent Errors ==="
journalctl -u $SERVICE -p err -n 5 --no-pager

echo -e "\n=== VRAM Usage ==="
curl -s -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://127.0.0.1:8767/models/status | jq '.utilization_pct'

echo -e "\n=== Loaded Models ==="
curl -s -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://127.0.0.1:8767/models/status | jq '.loaded[]'

echo -e "\n=== Last 10 Requests ==="
journalctl -u $SERVICE -n 10 --no-pager | grep "GET\|POST"
```

### Database Maintenance

**Check database size:**
```bash
du -h /var/www/ai-provider-service/ai_provider.db
```

**Backup database:**
```bash
cp /var/www/ai-provider-service/ai_provider.db \
   /var/www/ai-provider-service/ai_provider.db.backup.$(date +%Y%m%d)
```

**Database grows when:** Tracking API calls, storing configurations, managing queue

### Log Rotation

Systemd journal is managed by `journalctl`. To limit disk usage:

```bash
# Check journal size
journalctl --disk-usage

# Set max journal size (edit /etc/systemd/journald.conf)
# SystemMaxUse=500M
# Then restart:
systemctl restart systemd-journald
```

---

## Restart Scenarios

### Graceful Restart
```bash
systemctl restart ai-provider-service.service

# Verify it's running
systemctl status ai-provider-service.service
```

Service will:
1. Terminate current requests gracefully (timeout: 10 seconds per Flask request)
2. Wait `RestartSec=10` seconds
3. Start fresh with clean state

### After Server Reboot
```bash
# Service should start automatically
# Verify:
systemctl status ai-provider-service.service

# If not running, check why
journalctl -u ai-provider-service.service --since "5 minutes ago"
```

### After Crash
Service automatically restarts with 10-second delay between attempts. Check:
```bash
journalctl -u ai-provider-service.service -n 50 | grep -E "Started|Restart"
```

---

## Configuration Changes

**After modifying `.env`:**

1. Stop service
   ```bash
   systemctl stop ai-provider-service.service
   ```

2. Edit `.env`
   ```bash
   nano /var/www/ai-provider-service/.env
   ```

3. Restart service (loads new `.env`)
   ```bash
   systemctl restart ai-provider-service.service
   ```

4. Verify changes took effect
   ```bash
   systemctl status ai-provider-service.service
   curl -H "Authorization: Bearer $NEW_TOKEN" \
     http://127.0.0.1:8767/health
   ```

**After modifying systemd unit file:**

1. Reload systemd
   ```bash
   systemctl daemon-reload
   ```

2. Restart service
   ```bash
   systemctl restart ai-provider-service.service
   ```

3. Re-enable if needed
   ```bash
   systemctl enable ai-provider-service.service
   ```

---

## Performance Tuning

### High VRAM Usage

**Check current usage:**
```bash
curl -s -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://127.0.0.1:8767/models/status
```

**Unload unused models:**
```bash
curl -X POST \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model_name": "model-to-unload"}' \
  http://127.0.0.1:8767/models/unload
```

**Reduce VRAM allocation:**
1. Edit `.env` if there are model-specific settings
2. Use `STARTUP_MODE=lazy` (models load on-demand)
3. Restart service

### Slow Response Times

**Check service load:**
```bash
# Monitor in real-time
watch -n 1 'systemctl status ai-provider-service.service | grep -E "Memory|CPU"'

# Check provider response times
time curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://127.0.0.1:8767/models/status
```

**If CPU or memory high:**
1. Restart service: `systemctl restart ai-provider-service.service`
2. Check for stuck requests in logs
3. Consider spreading requests over time

---

## Disaster Recovery

### Service Won't Start After Configuration Change

```bash
# Revert .env to previous state
# Check git history
cd /var/www/ai-provider-service
git diff HEAD~1 .env

# Restore previous .env if needed
git checkout HEAD~1 -- .env

# Try starting again
systemctl start ai-provider-service.service
```

### Database Corruption

```bash
# Backup corrupted database
mv ai_provider.db ai_provider.db.corrupted

# Service will create new database on start
# Configurations will be lost — clients must re-register
systemctl restart ai-provider-service.service

# Restore from backup if available
# rm ai_provider.db
# cp ai_provider.db.backup.YYYYMMDD ai_provider.db
# systemctl restart ai-provider-service.service
```

### Complete Service Failure

```bash
# 1. Check system resources
free -h
df -h
ps aux | head -20

# 2. Check service logs for root cause
journalctl -u ai-provider-service.service -n 100

# 3. Force restart
systemctl stop ai-provider-service.service
sleep 5
systemctl start ai-provider-service.service

# 4. Verify
systemctl status ai-provider-service.service
curl -H "Authorization: Bearer $SERVICE_TOKEN" \
  http://127.0.0.1:8767/health

# 5. If still failing, check configuration
cat /var/www/ai-provider-service/.env
systemctl cat ai-provider-service.service
```

---

## Security Considerations

### SERVICE_TOKEN Rotation

When rotating `SERVICE_TOKEN`:

```bash
# 1. Generate new token
NEW_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# 2. Update .env
sed -i "s/SERVICE_TOKEN=.*/SERVICE_TOKEN=$NEW_TOKEN/" .env

# 3. Update all client applications with new token

# 4. Restart service once clients are updated
systemctl restart ai-provider-service.service

# 5. Verify with new token
curl -H "Authorization: Bearer $NEW_TOKEN" \
  http://127.0.0.1:8767/health
```

### ANTHROPIC_API_KEY Rotation

When rotating Anthropic API key:

```bash
# 1. Generate new key in Anthropic dashboard
# 2. Update .env
nano /var/www/ai-provider-service/.env
# Set: ANTHROPIC_API_KEY=sk-ant-...

# 3. Restart service
systemctl restart ai-provider-service.service

# 4. Verify Claude provider works
curl -X POST \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","provider":"claude","model":"claude-3-5-haiku-20241022","messages":[{"role":"user","content":"hi"}],"max_tokens":20}' \
  http://127.0.0.1:8767/chat
```

---

## Deploying the provider-access gate

Order matters — the bootstrap MUST run before flipping `GATE_ENABLED=true`,
or existing consumer apps will hit 403 for already-configured providers.

1. **Deploy code with `GATE_ENABLED=false`** (default). New table
   `provider_grants` is created via `db.create_all()` on service restart.
   No behavior change for existing callers.

2. **Set `ADMIN_TOKEN`, `ADMIN_USER_ID`, `SECRET_KEY`** in `.env` on the VPS:
   ```bash
   ssh ionos-vps
   cd /opt/ai-provider-service
   echo "ADMIN_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env
   echo "SECRET_KEY=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" >> .env
   echo "ADMIN_USER_ID=harald" >> .env
   ```

3. **Run the bootstrap** to grant every existing config:
   ```bash
   cd /opt/ai-provider-service
   source venv/bin/activate
   flask --app app grants-bootstrap
   # → "Created N new grants."
   ```

4. **Flip the gate on** and restart:
   ```bash
   echo "GATE_ENABLED=true" >> .env
   sudo systemctl restart ai-provider-service
   ```

5. **Smoke test:**
   ```bash
   # admin token — should work
   curl -H "Authorization: Bearer $ADMIN_TOKEN" https://<host>/admin/overview

   # existing consumer app on a grandfathered config — should still work
   curl -H "Authorization: Bearer $SERVICE_TOKEN" \
        -X POST https://<host>/configs/loganonymizer-default/ollama \
        -H 'Content-Type: application/json' -d '{"config":{}}'

   # new user_id on a gated provider — should 403
   curl -H "Authorization: Bearer $SERVICE_TOKEN" \
        -X POST https://<host>/configs/test-new-user/claude \
        -H 'Content-Type: application/json' -d '{"config":{"api_key":"x"}}'
   # → {"error":"needs_approval", ...}
   ```

6. **Verify admin UI** by visiting:
   ```
   https://<host>/admin/ui?token=<ADMIN_TOKEN>
   ```
   Should redirect to `/admin/ui/users` and show the roster.

### Rollback

If anything misbehaves:
1. `echo "GATE_ENABLED=false" >> .env` (or remove the line)
2. `sudo systemctl restart ai-provider-service`

Gate becomes a no-op, all existing callers work unchanged. Bootstrap rows
in `provider_grants` are harmless — they're ignored when gate is off.

## Contact & Escalation

**Service Down:** Check logs with `journalctl` command above, then restart with `systemctl restart ai-provider-service.service`

**Performance Issues:** Run health checks and check VRAM usage at `/models/status`

**Integration Failures:** Run `verify_integration.py` in client repo to diagnose

**Security Concerns:** Rotate tokens immediately and review logs for unauthorized access

---

## Markdown memory vault

**Source of truth:** SQLite (`memory_notes` and `summary_jobs` tables).

**Filesystem view:** `/var/lib/ai-provider-service/vault/` — regenerable
cache. *Excluded* from backups by design; if you lose it, run
`flask vault-render --rebuild` to recreate from DB.

**Background timers:**
- `ai-provider-summary.timer` — nightly @ 02:30 UTC. Logs:
  `/var/log/ai-provider-service/summary-job.log`.
- `ai-provider-vault-render.timer` — every 10 min. Logs:
  `/var/log/ai-provider-service/vault-render.log`.

**Feature flag:** `MEMORY_ENABLED` in `/etc/ai-provider-service/.env`.
Set to `false` to disable audit hook and put Memory API into 503 mode
without restarting.

**Disaster recovery for the vault directory:**

```bash
sudo rm -rf /var/lib/ai-provider-service/vault/*
sudo -u ai-provider /opt/ai-provider-service/venv/bin/flask --app app vault-render --rebuild
```

**Obsidian / local access:**

The vault is host-mounted at `/var/lib/ai-provider-service/vault/`.
Access it from a local machine:

```bash
# Via rsync (one-shot pull):
rsync -avz ionos-vps:/var/lib/ai-provider-service/vault/ ./vault/

# Via SSHFS (live mount — requires sshfs):
mkdir -p ~/vault
sshfs ionos-vps:/var/lib/ai-provider-service/vault ~/vault
# Then open ~/vault in Obsidian as a vault folder.

# To unmount:
fusermount -u ~/vault
```

**Rate limits** (Phase 1.5, live):
| Bucket | Limit | Window |
|---|---|---|
| `memory:write` (POST notes/events/summarize) | 60 | 1 min |
| `memory:read` (GET notes) | 120 | 1 min |
| `vault:export` (GET vault.tar.gz) | 5 | 1 min |

Rate limit hits are logged at WARNING level with bucket, user, and path.

**Deploy of Phase 1 (one-time):**

```bash
# 1. Pull branch, install unit files
sudo cp deploy/ai-provider-summary.{service,timer} /etc/systemd/system/
sudo cp deploy/ai-provider-vault-render.{service,timer} /etc/systemd/system/

# 2. Create vault dir + SELinux context
sudo install -d -o ai-provider -g ai-provider -m 0750 /var/lib/ai-provider-service/vault
sudo semanage fcontext -a -t var_lib_t '/var/lib/ai-provider-service/vault(/.*)?'
sudo restorecon -Rv /var/lib/ai-provider-service/vault

# 3. Update .env (add MEMORY_ENABLED=true and the other keys)

# 4. Restart service + enable timers
sudo systemctl restart ai-provider-service
sudo systemctl daemon-reload
sudo systemctl enable --now ai-provider-summary.timer ai-provider-vault-render.timer
sudo systemctl list-timers | grep ai-provider
```

