#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# © 2026 Harald Weiss
#
# scripts/set-env-key.sh — add or rotate one KEY=VALUE line in the
# ai-provider-service env file on oracle-vm.
#
# Why this exists (2026-10-18): OpenCode invalidated the Console API key after
# the breach, so the key had to be rotated in a root-owned env file on the VM.
# The naive form — `ssh oracle-vm "echo KEY=<secret> >> /etc/.../ai-provider.env"`
# — leaks the secret into the ssh argv (readable via `ps` on the VM), the local
# shell history, and any session transcript. This script instead:
#   * reads the secret from a hidden prompt (or stdin),
#   * pipes it over ssh stdin (never in a command line locally or remotely),
#   * passes it to the remote script base64-encoded inside the script body,
#   * backs the env file up, replaces any existing line for that variable,
#     writes it atomically with mode 600, and
#   * verifies by fingerprint (sha256 prefix) + length, then runs a live probe.
# The plaintext only ever exists in memory and in the 0600 env file.
#
# Usage:
#   scripts/set-env-key.sh                             # OPENCODE_GO_API_KEY
#   scripts/set-env-key.sh OPENCODE_API_KEY
#   scripts/set-env-key.sh --no-probe OPENCODE_GO_API_KEY
#   printf '%s' "$KEY_FROM_VAULT" | scripts/set-env-key.sh
#
# Options:
#   --host HOST       ssh target              (default: oracle-vm, $AIPS_HOST)
#   --env-file PATH   remote env file         (default: /etc/ai-provider/ai-provider.env)
#   --base-url URL    probe base URL          (default: Go/Zen endpoint per variable)
#   --no-probe        skip the live auth probe
#   -h, --help        this text
#
# Exit codes: 0 ok · 2 usage/validation · 3 remote write failed
#             4 verification mismatch · 5 live probe failed
#
# Env changes are only read when the container is created, so recreate the
# container afterwards (AGENTS.md §6) — the table also records that env_file
# edits need `up -d --force-recreate`, not `restart`.

set -euo pipefail

HOST="${AIPS_HOST:-oracle-vm}"
ENV_FILE="${AIPS_ENV_FILE:-/etc/ai-provider/ai-provider.env}"
DO_PROBE=1
BASE_URL=""
VAR=""

usage() {
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host)     HOST="$2"; shift 2 ;;
        --env-file) ENV_FILE="$2"; shift 2 ;;
        --base-url) BASE_URL="$2"; shift 2 ;;
        --no-probe) DO_PROBE=0; shift ;;
        -h|--help)  usage; exit 0 ;;
        -*)         echo "unknown option: $1" >&2; exit 2 ;;
        *)          VAR="$1"; shift ;;
    esac
done
VAR="${VAR:-OPENCODE_GO_API_KEY}"

# --- validate the things that end up inside the remote command line ----------
case "$VAR" in
    *[!A-Za-z0-9_]*) echo "invalid variable name: $VAR" >&2; exit 2 ;;
esac
case "$ENV_FILE" in
    *[!A-Za-z0-9/._-]*) echo "unsafe --env-file path: $ENV_FILE" >&2; exit 2 ;;
esac

# --- read the secret (hidden prompt or stdin) --------------------------------
if [ -t 0 ]; then
    printf 'Value for %s (input hidden, then Enter): ' "$VAR" >&2
    IFS= read -rs SECRET || { echo >&2; echo 'aborted (no input)' >&2; exit 2; }
    echo >&2
else
    SECRET="$(cat)"
fi

[ -n "$SECRET" ] || { echo 'refusing to write an empty value' >&2; exit 2; }
case "$SECRET" in
    *[[:space:]]*) echo 'value must not contain whitespace' >&2; exit 2 ;;
    *'='*)         echo 'value must not contain "="' >&2; exit 2 ;;
esac
if [ "${#SECRET}" -lt 8 ]; then
    echo "value is only ${#SECRET} chars — refusing (looks truncated)" >&2; exit 2
fi

fp_of() {
    # sha256 prefix of the value; never prints the value itself
    if command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$1" | sha256sum | cut -c1-12
    else
        printf '%s' "$1" | shasum -a 256 | cut -c1-12
    fi
}
LOCAL_FP="$(fp_of "$SECRET")"
B64="$(printf '%s' "$SECRET" | base64 | tr -d '\n')"

echo "▶ ${HOST}: $VAR → $ENV_FILE (len=${#SECRET}, fp=${LOCAL_FP})"

# --- remote: backup, replace line atomically, mode 600 ----------------------
# The script is fed over stdin; $SECRET_B64 is defined by the first line.
# Nothing secret is passed as an argument, so it cannot show up in `ps`.
REMOTE_SCRIPT=$(cat <<'REMOTE'
set -eu
VAR="$1"
ENV_FILE="$2"
secret=$(printf '%s' "$SECRET_B64" | base64 -d)

[ -f "$ENV_FILE" ] || { echo "REMOTE FAIL: $ENV_FILE does not exist" >&2; exit 3; }

ts=$(date -u +%Y%m%d%H%M%S)
backup="/root/$(basename "$ENV_FILE").bak-$ts"
cp -a "$ENV_FILE" "$backup"

tmp=$(mktemp "${ENV_FILE}.XXXXXX")
case "$tmp" in "${ENV_FILE}".*) ;; *) echo "REMOTE FAIL: unsafe mktemp result" >&2; exit 3 ;; esac

# Drop every previous line for this variable (avoids duplicate definitions,
# where the last one would silently win), then append the new value.
grep -v "^${VAR}=" "$ENV_FILE" > "$tmp" || true
printf '%s=%s\n' "$VAR" "$secret" >> "$tmp"
chmod 600 "$tmp"
chown root:root "$tmp" 2>/dev/null || true
mv "$tmp" "$ENV_FILE"

fp=$(printf '%s' "$secret" | sha256sum | cut -c1-12)
printf 'WROTE fp=%s len=%s backup=%s\n' "$fp" "${#secret}" "$backup"
REMOTE
)

set +e
REMOTE_OUT=$( { printf "SECRET_B64='%s'\n" "$B64"; printf '%s\n' "$REMOTE_SCRIPT"; } \
    | ssh "$HOST" "sudo sh -s -- '$VAR' '$ENV_FILE'" 2>&1 )
REMOTE_RC=$?
set -e
if [ "$REMOTE_RC" -ne 0 ]; then
    echo "✗ remote write failed (rc=$REMOTE_RC):" >&2
    printf '%s\n' "$REMOTE_OUT" >&2
    exit 3
fi
REMOTE_FP="$(printf '%s\n' "$REMOTE_OUT" | sed -n 's/.*WROTE fp=\([0-9a-f]*\).*/\1/p' | tail -1)"
REMOTE_LEN="$(printf '%s\n' "$REMOTE_OUT" | sed -n 's/.*len=\([0-9]*\).*/\1/p' | tail -1)"
BACKUP="$(printf '%s\n' "$REMOTE_OUT" | sed -n 's/.*backup=\([^ ]*\).*/\1/p' | tail -1)"

if [ -z "$REMOTE_FP" ] || [ "$REMOTE_FP" != "$LOCAL_FP" ] || [ "$REMOTE_LEN" != "${#SECRET}" ]; then
    echo '✗ verification mismatch — file not (or wrongly) written' >&2
    printf '%s\n' "$REMOTE_OUT" >&2
    exit 4
fi
echo "✓ written (fp=$REMOTE_FP, len=$REMOTE_LEN) · backup: ${BACKUP:-none}"

# --- live probe: does the stored value actually authenticate? ---------------
if [ "$DO_PROBE" -eq 1 ]; then
    if [ -z "$BASE_URL" ]; then
        case "$VAR" in
            OPENCODE_GO_API_KEY) BASE_URL='https://opencode.ai/zen/go/v1' ;;
            OPENCODE_API_KEY)    BASE_URL='https://opencode.ai/zen/v1' ;;
            *) DO_PROBE=0 ;;
        esac
    fi
fi

if [ "$DO_PROBE" -eq 1 ]; then
    PROBE_PY="$(mktemp "${TMPDIR:-/tmp}/aips-env-key-probe.XXXXXX")"
    trap 'rm -f "$PROBE_PY"' EXIT
    cat > "$PROBE_PY" <<'PY'
"""Live auth probe: reads the stored value from the env file (never argv)."""
import json
import re
import sys
import urllib.error
import urllib.request

var, base, env_file = sys.argv[1], sys.argv[2].rstrip('/'), sys.argv[3]

token = ''
with open(env_file) as fh:
    for line in fh:
        m = re.match(r'^([A-Za-z0-9_]+)=(.*)$', line.strip())
        if m and m.group(1) == var:
            token = m.group(2).strip().strip('"').strip("'")
if not token:
    print(f'PROBE FAIL: no value for {var} in {env_file}')
    sys.exit(5)


def call(url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {
        'Authorization': 'Bearer ' + token,
        'User-Agent': 'ai-provider-service/env-key-probe',
    }
    if data is not None:
        headers['Content-Type'] = 'application/json'
        headers['x-opencode-session'] = 'env-key-probe-0001'
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, data=body, headers=headers),
                timeout=90) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:200]
    except Exception as exc:  # noqa: BLE001 — report, don't traceback
        return 0, f'{type(exc).__name__}: {exc}'.encode()


status, body = call(base + '/models')
if status != 200:
    print(f'PROBE FAIL {base}/models -> HTTP {status}: '
          f'{body[:180].decode("utf-8", "replace")}')
    sys.exit(5)
models = [m['id'] for m in json.loads(body).get('data', [])]
print(f'PROBE ok /models HTTP 200 · {len(models)} models')

# Prefer a model that exists on both Go and Console (Zen) endpoints.
model = 'deepseek-v4.1-flash' if 'deepseek-v4.1-flash' in models else (models[0] if models else '')
if not model:
    sys.exit(0)
status, body = call(base + '/chat/completions', {
    'model': model,
    'messages': [{'role': 'user', 'content': 'reply with exactly: OK'}],
    'max_tokens': 16,
})
if status != 200:
    print(f'PROBE FAIL chat ({model}) -> HTTP {status}: '
          f'{body[:180].decode("utf-8", "replace")}')
    sys.exit(5)
try:
    text = json.loads(body)['choices'][0]['message'].get('content') or ''
except Exception:  # noqa: BLE001 — shape varies per provider
    text = ''
print(f'PROBE ok chat HTTP 200 · model={model} content={text[:40]!r}')
PY
    set +e
    ssh "$HOST" "sudo python3 - '$VAR' '$BASE_URL' '$ENV_FILE'" < "$PROBE_PY"
    PROBE_RC=$?
    set -e
    if [ "$PROBE_RC" -ne 0 ]; then
        echo "✗ live probe failed (rc=$PROBE_RC) — value is stored, but upstream rejects it" >&2
        exit 5
    fi
fi

echo
echo "next: recreate the container so the env is re-read (AGENTS.md §6):"
echo "  cd /opt/ai-provider-service && sudo docker compose up -d --force-recreate ai-provider"
