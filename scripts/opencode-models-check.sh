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
