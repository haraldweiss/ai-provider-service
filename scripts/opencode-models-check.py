#!/usr/bin/env python3
"""Compare current opencode.ai models with cached state, email changes."""
import json
import os
import subprocess
import sys
import time

CACHE_FILE = sys.argv[1] if len(sys.argv) > 1 else '/tmp/opencode_all_models.json'
PREV_FILE = sys.argv[2] if len(sys.argv) > 2 else '/tmp/opencode_all_models.json.prev'
NOTIFY_EMAIL = 'harald.weiss@wolfinisoftware.de'

data = json.load(sys.stdin)
models = {}
for m in data.get('data', []):
    mid = m['id']
    if mid == 'big-pickle':
        continue
    models[mid] = 'free' if mid.endswith('-free') else 'paid'

now = time.time()

old = {}
if os.path.exists(CACHE_FILE):
    try:
        old = json.load(open(CACHE_FILE)).get('models', {})
    except Exception:
        pass

old_set = set(old.keys())
new_set = set(models.keys())

added = new_set - old_set
removed = old_set - new_set

if not added and not removed:
    json.dump({'ts': now, 'models': models}, open(CACHE_FILE, 'w'))
    print('No model changes')
    sys.exit(0)

free_added = sorted(m for m in added if models[m] == 'free')
paid_added = sorted(m for m in added if models[m] == 'paid')
free_removed = sorted(m for m in removed if old.get(m) == 'free')
paid_removed = sorted(m for m in removed if old.get(m) == 'paid')

lines = []
if free_added:
    lines.append('Neue Free-Modelle:')
    lines.extend(f'  + {m}' for m in free_added)
    lines.append('')
if paid_added:
    lines.append('Neue Paid-Modelle:')
    lines.extend(f'  + {m}' for m in paid_added)
    lines.append('')
if free_removed:
    lines.append('Entfernte Free-Modelle:')
    lines.extend(f'  - {m}' for m in free_removed)
    lines.append('')
if paid_removed:
    lines.append('Entfernte Paid-Modelle:')
    lines.extend(f'  - {m}' for m in paid_removed)
    lines.append('')

free_count = sum(1 for m in models.values() if m == 'free')
paid_count = sum(1 for m in models.values() if m == 'paid')
lines.append(f'Gesamt: {len(models)} Modelle ({free_count} free, {paid_count} paid)')

body = '\n'.join(lines)
subject = 'opencode.ai: Modell-Aenderungen entdeckt'

msg = (
    f'Subject: {subject}\n'
    f'From: ai-provider@wolfinisoftware.de\n'
    f'To: {NOTIFY_EMAIL}\n'
    f'\n'
    f'{body}\n'
)
subprocess.run(['/usr/sbin/sendmail', '-t'], input=msg, capture_output=True, timeout=10, text=True)
print('Email sent:', subject)
print(body)

if os.path.exists(CACHE_FILE):
    os.replace(CACHE_FILE, PREV_FILE)
json.dump({'ts': now, 'models': models}, open(CACHE_FILE, 'w'))
