# Colibri Mac Studio POC operations

These are reproducible, secret-free templates for a **reversible** Colibri POC.
They create a dedicated local Colibri listener on `127.0.0.1:18000`, a separate
reverse SSH tunnel to oracle-vm, and a host bridge bound only to Docker's host
gateway `172.17.0.1:18000`. They do not edit, replace, or delete an Ollama
LaunchAgent, tunnel, or model.

Do not enable the gateway until every preflight and acceptance check has passed.
The Colibri API key belongs only in the root-owned gateway environment file and
in the mode-600 Mac environment file; never commit, log, print, or paste it.

## Preflight: before any model download

Run this exact read-only sequence first:

```bash
COLIBRI_MODEL_DIR=/Volumes/ColibriModels/glm52_i4
sysctl -n hw.memsize
df -h "$COLIBRI_MODEL_DIR"
diskutil info "$COLIBRI_MODEL_DIR"
pgrep -alf 'ollama|coli|glm' || true
launchctl print gui/$(id -u)/com.wolfini.ollama-tunnel 2>/dev/null || true
```

Use the shown `COLIBRI_MODEL_DIR` only **after** that directory has been created
on the Mac Studio's stable, directly attached model volume. Confirm at least
**400 GB free** there. Record only aggregate capacity and mount information;
do not place an API key, complete environment file, prompt, or personal path in
an evidence report.

Before gateway enablement, the owner must run the Colibri installation's
`./setup.sh`, then:

```bash
COLI_MODEL="$COLIBRI_MODEL_DIR" ./coli doctor
COLI_MODEL="$COLIBRI_MODEL_DIR" ./coli iobench
```

Treat any competing Ollama process as a hold condition. Ollama may be stopped
or rerouted only for a measurement window; no existing model or tunnel is
deleted as part of this POC.

## Secret handling and template preparation

1. On the Mac Studio, create the local, non-repository environment file at
   `~/.config/colibri/colibri.env`, make it mode `600`, and set only the
   runtime's `COLI_MODEL` and `COLI_API_KEY` there. Do not store it under a
   model volume, under `~/.ollama`, or in this repository.
2. Create `~/Library/Logs/colibri/` on the Mac's internal disk. In both plist
   templates, replace `REPLACE_WITH_MACOS_USERNAME` with the actual macOS user
   name. launchd log paths must remain on internal disk, never under
   `~/.ollama` or an external model volume.
3. Copy the two templates to `~/Library/LaunchAgents/`. They are new jobs:
   `com.wolfini.colibri` and `com.wolfini.colibri-tunnel`. The service template
   loads the protected local environment file before it runs Colibri. The
   tunnel uses a distinct reverse forward `18000:127.0.0.1:18000` to
   `opc@oracle-vm`; it does not alter the Ollama tunnel.
4. On oracle-vm, install the bridge template as
   `/etc/systemd/system/ai-provider-colibri-bridge.service`. It binds only
   `172.17.0.1:18000` and forwards to the loopback-only reverse tunnel. It must
   never be changed to `0.0.0.0` or `[::]`.
5. Add the redacted `COLIBRI_*` settings to `/etc/ai-provider/ai-provider.env`
   only after the local and bridge checks succeed. Set `COLIBRI_ENABLED=true`
   only for the immutable gateway deployment. The gateway environment file is
   root-owned and mode 600; the API key is not a shell command argument.

Use `launchctl kickstart -k` for the two new jobs after installation. Do not
operate the existing Ollama job. On oracle-vm, reload systemd and enable only
the new bridge service. These are operator-run instructions, not an approval to
perform a production rollout from this repository checkout.

## Required local, tunnel, and bridge checks

After Colibri has started, run these checks. The first three execute on the
Mac Studio; the last executes on oracle-vm after the bridge is running.

```bash
curl -fsS -H "Authorization: Bearer $COLI_API_KEY" http://127.0.0.1:18000/health
lsof -nP -iTCP:18000 -sTCP:LISTEN
curl -fsS -H "Authorization: Bearer $COLI_API_KEY" http://127.0.0.1:18000/v1/models
ss -tln | grep '172\.17\.0\.1:18000'
```

The listener check must show only loopback on the Mac and only
`172.17.0.1:18000` for the bridge. A `0.0.0.0:18000` or `[::]:18000` listener
is a failed POC: stop before adding `COLIBRI_ENABLED=true`.

## Gateway configuration

The gateway consumes these values from `/etc/ai-provider/ai-provider.env`:

| Variable | POC value | Meaning |
| --- | --- | --- |
| `COLIBRI_ENABLED` | `false` initially | Enables the admin-only Colibri provider only after acceptance. |
| `COLIBRI_BASE_URL` | `http://host.docker.internal:18000` | Docker-to-host bridge address; do not use a public address. |
| `COLIBRI_API_KEY` | unset in templates | Secret shared only with the local Colibri environment file. |
| `COLIBRI_MODEL_ID` | `glm-5.2-colibri` | Fixed POC model identifier. |
| `COLIBRI_CONNECT_TIMEOUT_SEC` | `10` | Upstream connection timeout in seconds. |
| `COLIBRI_STREAM_READ_TIMEOUT_SEC` | `180` | Per-stream read timeout in seconds. |

The launchd template, not the gateway environment, owns global admission with
`coli serve --max-queue 1 --queue-timeout 60`. The single Colibri service
therefore enforces one active inference and one waiting request consistently
across both Gunicorn workers. The gateway opens each request directly; it maps
the service's OpenAI-shaped 429 `queue_full` and `queue_timeout` codes, and has
no local semaphore or waiter counter. Colibri is never added to the persistent
`request_queue` and never falls back to another provider.

## Rollback

Rollback is deliberately narrow. Stop/unload **only** the two new launchd jobs
`com.wolfini.colibri` and `com.wolfini.colibri-tunnel`; stop/disable **only**
`ai-provider-colibri-bridge.service`. Remove `COLIBRI_ENABLED` from the
gateway environment, recreate the prior SHA-tagged gateway image, and restore
the prior Ollama routing. Do not stop, unload, edit, or delete an existing
Ollama LaunchAgent/tunnel outside the temporary measurement routing change.

Model files remain in place until the owner separately asks for their deletion.
Do not remove the model directory during rollback.

Suggested operator sequence:

```bash
# Mac Studio: only the two new POC jobs
launchctl bootout gui/$(id -u)/com.wolfini.colibri
launchctl bootout gui/$(id -u)/com.wolfini.colibri-tunnel

# oracle-vm: only the new Colibri bridge
sudo systemctl disable --now ai-provider-colibri-bridge.service

# gateway: remove COLIBRI_ENABLED from its root-owned env file, then recreate
# the previously recorded SHA-tagged image with Docker Compose.
```

Use the gateway deployment procedure in the repository root README and record
the previous image SHA before enabling Colibri. A plain container restart does
not reread environment changes.
