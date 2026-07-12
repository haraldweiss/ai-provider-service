#!/bin/bash
# Check Cline OSS model catalog for changes to free/ClinePass models
# Usage: ./scripts/check-cline-catalog.sh

set -euo pipefail
cd "$(dirname "$0")/.."

CATALOG_URL="https://raw.githubusercontent.com/cline/cline/main/sdk/packages/llms/src/catalog/catalog.generated.ts"
OVERRIDE_FILE="pricing_overrides_cline.json"
TMP_CATALOG=$(mktemp)
trap 'rm -f "$TMP_CATALOG"' EXIT

echo "🔍 Fetching latest Cline model catalog..."
if ! curl -sS --connect-timeout 10 "$CATALOG_URL" -o "$TMP_CATALOG"; then
    echo "❌ Failed to fetch catalog"
    exit 1
fi
echo "   $(wc -c < "$TMP_CATALOG") bytes"

python3 << 'PYEOF'
import re, json, sys
from pathlib import Path

BASE = Path('.')
CATALOG_FILE = Path('/tmp/cline-catalog.ts')
OVERRIDE_FILE = BASE / "pricing_overrides_cline.json"

raw = CATALOG_FILE.read_text()
lines = raw.split('\n')

# Parse all model IDs from catalog
catalog_models = {}
for i, line in enumerate(lines):
    m = re.match(r'^\s*id:\s*["\']([^"\']+)["\'],\s*$', line)
    if not m:
        continue
    mid = m.group(1)
    catalog_models[mid] = {
        'is_free': ':free' in mid or mid.endswith(':free'),
        'is_pass': mid.startswith('cline-pass/'),
    }

# Read current override
with open(OVERRIDE_FILE) as f:
    override_data = json.load(f)

override_ids = set(k.split('::')[1] if '::' in k else k for k in override_data)

pass_ids = {m for m, info in catalog_models.items() if info['is_pass']}
free_ids = {m for m, info in catalog_models.items() if info['is_free']}
override_pass = {m for m in override_ids if m.startswith('cline-pass/')}
override_free = {m for m in override_ids if ':free' in m or m.endswith(':free')}

issues = []

# ClinePass changes
new_pass = pass_ids - override_pass
missing_pass = override_pass - pass_ids
if new_pass:
    issues.append(f"Neue ClinePass-Modelle ({len(new_pass)}): {', '.join(sorted(new_pass))}")
if missing_pass:
    issues.append(f"ClinePass-Modelle entfernt ({len(missing_pass)}): {', '.join(sorted(missing_pass))}")

# Free model changes
new_free = free_ids - override_free
missing_free = override_free - free_ids
if new_free:
    issues.append(f"Neue Free-Modelle ({len(new_free)}): {', '.join(sorted(new_free))}")
if missing_free:
    issues.append(f"Free-Modelle entfernt ({len(missing_free)}): {', '.join(sorted(missing_free))}")

# Count change
if len(catalog_models) != len(override_data):
    issues.append(f"Model-Count: Katalog {len(catalog_models)} vs Override {len(override_data)}")

if issues:
    print("CHANGED")
    for i in issues:
        print(f"  {i}")
    print(f"  Katalog: {len(catalog_models)} | Free: {len(free_ids)} | ClinePass: {len(pass_ids)}")
    print(f"  Override: {len(override_data)} | Free: {len(override_free)} | ClinePass: {len(override_pass)}")
    sys.exit(1)
else:
    print("OK")
    print(f"  {len(catalog_models)} Modelle, {len(pass_ids)} ClinePass, {len(free_ids)} Free — unverändert")
    sys.exit(0)
PYEOF
