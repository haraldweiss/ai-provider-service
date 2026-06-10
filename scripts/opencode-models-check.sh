#!/bin/bash
# Prueft taeglich auf neue/entfernte Modelle bei opencode.ai
# Sendet Email mit Unterteilung in Free und Paid.
TOKEN=$(grep OPENCODE_API_KEY /etc/ai-provider/ai-provider.env | cut -d= -f2-)
CACHE="/tmp/opencode_all_models.json"
PREV="/tmp/opencode_all_models.json.prev"

DATA=$(curl -s --max-time 15 "https://opencode.ai/zen/v1/models" -H "Authorization: Bearer $TOKEN")

if [ -z "$DATA" ]; then
  echo "FAILED to fetch models" >&2
  exit 1
fi

python3 /usr/local/bin/opencode-models-check.py "$CACHE" "$PREV" <<< "$DATA"

# Free model cache im ai-provider-Service aktualisieren
flock -n /tmp/opencode_free_models.lock \
  docker exec ai-provider flask --app app refresh-free-models 2>&1 \
  || echo 'Free model refresh skipped (lock held or container busy)'
