---
name: ai-provider-workflow
description: Use when changing, reviewing, deploying, or operating ai-provider-service; covers encrypted provider keys, Docker image deploys, oracle-vm runtime, SQLite/vault paths, Ollama tunnels, no-container-hotfix discipline, and production verification.
---

# AI Provider Workflow

Use this skill for any ai-provider-service code, provider integration, auth/key handling, database, Docker, tunnel, deploy, or production investigation task.

## Core Rules

- Never log or expose decrypted provider keys. Fernet keys come from `FERNET_KEY`, never source.
- Do not hardcode SQLite or vault paths. Use environment/config.
- Ollama access goes through reverse-SSH tunnels and socat bridges. Always handle connection failures with provider fallback.
- Health checks must be parallel/non-blocking.
- Gunicorn runs inside the Docker container behind host Apache. Do not bind directly to 80/443.
- The Markdown vault is rendered cache; DB tables are source of truth.
- Do not leave persistent fixes inside the running container via `sed`, `docker cp`, or `docker exec`. Incident hotfixes must be committed, rebuilt, and redeployed so running equals committed.
- Personal provider keys are identity-bound and must not silently fall back to server-funded keys.

## Verification

For normal changes, run the relevant local test suite, usually:

```bash
pytest
```

Before production deploys, ensure CI-equivalent checks are green, build with `build.sh` for SHA-tagged images, recreate the container when env changes, and capture live evidence such as `/health`, container image tag/created date, DB query success, bridge/proxy status, and absence of startup tracebacks.

## Production Reminders

- Host: `oracle-vm`.
- Container: `ai-provider`.
- Env: `/etc/ai-provider/ai-provider.env` is root-owned and read only at container creation time.
- DB: Docker volume `bewerbungen_data`, in-container `/app/data/storage.db`.
- Network: `bewerbungen-net`.

Read `AGENTS.md` and `README.md` before deploy or runtime changes.
