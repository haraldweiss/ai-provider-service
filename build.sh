#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# © 2026 Harald Weiss
#
# Baut das ai-provider-service Image MIT VERSION-Tag, nicht nur :latest.
# Grund (Session 2026-06-12): mit nacktem :latest ist "was laeuft" nicht
# eindeutig und ein Rollback unmoeglich.
#
# Verwendung:
#   ./build.sh             # Tag = git short-SHA (oder Timestamp ohne .git)
#   ./build.sh 22f0fbf     # expliziter Tag (z.B. Build aus git archive)
#
# Erzeugt zusaetzlich immer :latest. Rollback:
#   docker stop ai-provider && docker rm ai-provider
#   docker run -d --name ai-provider ... localhost/ai-provider:<alter-tag>
# (Run-Command s. AGENTS.md §6 Production access reference.)

set -euo pipefail

IMAGE_REPO="localhost/ai-provider"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "${1:-}" != "" ]; then
    TAG="$1"
elif git -C "$SCRIPT_DIR" rev-parse --short HEAD >/dev/null 2>&1; then
    TAG="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD)"
else
    TAG="$(date +%Y%m%d-%H%M%S)"
fi

echo "▶ Baue $IMAGE_REPO:$TAG (+ :latest)"
docker build -t "$IMAGE_REPO:$TAG" -t "$IMAGE_REPO:latest" "$SCRIPT_DIR"

echo "✓ Gebaut: $IMAGE_REPO:$TAG  (+ :latest)"
