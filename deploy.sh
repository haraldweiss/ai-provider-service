#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# © 2026 Harald Weiss
#
# Deploy-Skript: baut ai-provider-service auf oracle-vm und startet neu.
#
# Verwendung:
#   ./deploy.sh                              # rsync + build + restart
#   ./deploy.sh --skip-sync                  # nur build + restart (falls source schon da)
#   ./deploy.sh --restart                     # nur restart (ohne build)
#   ./deploy.sh --env-only                    # nur env sync + restart
#
# Voraussetzungen:
#   - SSH-Host "oracle-vm" in ~/.ssh/config
#   - docker compose auf oracle-vm (v5.2.0+)
#   - /etc/ai-provider/ai-provider.env existiert auf oracle-vm

set -euo pipefail

REMOTE_HOST="oracle-vm"
REMOTE_DIR="/opt/ai-provider-service"
COMPOSE_FILE="docker-compose.yml"

SKIP_SYNC=false
RESTART_ONLY=false
ENV_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --skip-sync) SKIP_SYNC=true ;;
        --restart)   RESTART_ONLY=true ;;
        --env-only)  ENV_ONLY=true ;;
    esac
done

echo "▶ ai-provider-service deploy"

if [ "$RESTART_ONLY" = true ]; then
    echo "  → Restart only"
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && docker compose down && docker compose up -d"
    echo "✓ Restart done"
    exit 0
fi

if [ "$ENV_ONLY" = true ]; then
    echo "  → Env sync only"
    if [ -f .env ]; then
        scp .env "$REMOTE_HOST:/etc/ai-provider/ai-provider.env"
        ssh "$REMOTE_HOST" "chmod 600 /etc/ai-provider/ai-provider.env"
        echo "✓ Env synced"
    else
        echo "! Keine .env-Datei lokal — überspringe"
    fi
    exit 0
fi

# ── Sync ─────────────────────────────────────────────
if [ "$SKIP_SYNC" = false ]; then
    echo "  → rsync source to $REMOTE_HOST:$REMOTE_DIR"
    rsync -avz --delete \
        --exclude='.git' --exclude='__pycache__' --exclude='.pytest_cache' \
        --exclude='venv' --exclude='.venv' --exclude='instance' \
        --exclude='.DS_Store' --exclude='.serena' --exclude='.claude' \
        --exclude='*.pyc' --exclude='*.swp' --exclude='.env' \
        ./ "$REMOTE_HOST:$REMOTE_DIR/"
    echo "✓ Sync done"
fi

# ── Build ─────────────────────────────────────────────
echo "  → docker compose build"
ssh "$REMOTE_HOST" "cd $REMOTE_DIR && docker compose build --pull 2>&1" | tail -5

# ── Restart ──────────────────────────────────────────
echo "  → docker compose up -d"
ssh "$REMOTE_HOST" "cd $REMOTE_DIR && docker compose down && docker compose up -d"

# ── Wait + verify ────────────────────────────────────
echo "  → Waiting for container to become healthy..."
sleep 8
HEALTH=$(ssh "$REMOTE_HOST" "docker inspect ai-provider --format '{{.State.Health.Status}}' 2>/dev/null || echo 'unknown'")
echo "  → Health: $HEALTH"

echo ""
echo "✓ Deploy done — $HEALTH"
