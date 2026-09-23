"""Daily provider-rule documentation watcher.

Provider APIs change their client rules (required headers, free-tier policy,
model endpoints) without notice — e.g. OpenCode Go made
``x-opencode-session`` mandatory and later started UA-gating its free tier.
This module fetches the documentation pages that define those rules, keeps a
snapshot of the rule-relevant lines and reports any change so
``providers/opencode.py`` / ``providers/cline.py`` and ``AGENTS.md`` §3.12 can
be adjusted.

Run daily via ``flask check-provider-docs`` (see AGENTS.md §6).
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)

NOTIFY_EMAIL = 'harald.weiss@wolfinisoftware.de'

# Documentation pages that define how our provider clients must behave.
PROVIDER_DOC_SOURCES = [
    {'id': 'opencode-go', 'label': 'OpenCode Go', 'url': 'https://opencode.ai/docs/go/'},
    {'id': 'opencode-zen', 'label': 'OpenCode Zen', 'url': 'https://opencode.ai/docs/zen/'},
    {'id': 'cline-auth', 'label': 'Cline API authentication',
     'url': 'https://docs.cline.bot/api/authentication'},
    {'id': 'cline-models', 'label': 'Cline API models',
     'url': 'https://docs.cline.bot/api/models'},
]

# Lines containing any of these terms describe a client rule we implement.
RULE_WATCH_TERMS = (
    'user-agent',
    'user agent',
    'x-opencode-session',
    'x-opencode-client',
    'http-referer',
    'x-title',
    'x-task-id',
    'session id',
    'session-id',
    'free tier',
    'free model',
    'free usage',
)


def snapshot_path() -> Path:
    return Path(Config.PROVIDER_DOCS_SNAPSHOT)


def fetch_text(url: str, timeout: int = 30) -> str:
    """Fetch a documentation page as UTF-8 text (never raises KeyError)."""
    req = urllib.request.Request(
        url,
        headers={'User-Agent': f'ai-provider-service/{Config.SERVICE_VERSION} '
                               f'(provider-docs-watch)'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode('utf-8', errors='replace')


def html_to_text(markup: str) -> str:
    """Crude HTML → text conversion; enough to diff documentation prose."""
    text = re.sub(r'(?is)<(script|style|svg|noscript)[^>]*>.*?</\1>', ' ', markup)
    text = re.sub(r'(?s)<[^>]+>', '\n', text)
    text = html.unescape(text)
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if line)


def extract_rule_lines(text: str) -> list[str]:
    """Return the documentation lines that mention a rule we care about."""
    found: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(term in low for term in RULE_WATCH_TERMS) and line not in found:
            found.append(line)
    return found


def _rule_hash(rule_lines: list[str]) -> str:
    return hashlib.sha256('\n'.join(rule_lines).encode('utf-8')).hexdigest()


def snapshot_source(source: dict, text: str) -> dict:
    rules = extract_rule_lines(text)
    return {
        'label': source['label'],
        'url': source['url'],
        'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'hash': _rule_hash(rules),
        'rules': rules,
    }


def load_snapshot(path: Path | None = None) -> dict:
    path = path or snapshot_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
    return {}


def save_snapshot(snapshot: dict, path: Path | None = None) -> Path:
    path = path or snapshot_path()
    if path.exists():
        try:
            path.replace(path.with_suffix(path.suffix + '.prev'))
        except OSError:
            pass
    path.write_text(json.dumps(snapshot, indent=2))
    return path


def diff_snapshots(old: dict, new: dict) -> dict:
    """Per-source added/removed rule lines (only for sources present in both)."""
    old_sources = old.get('sources', {})
    new_sources = new.get('sources', {})
    diff: dict = {}
    for sid, new_src in new_sources.items():
        old_src = old_sources.get(sid)
        if not old_src:
            continue
        old_rules = old_src.get('rules', [])
        new_rules = new_src.get('rules', [])
        added = [r for r in new_rules if r not in old_rules]
        removed = [r for r in old_rules if r not in new_rules]
        if added or removed:
            diff[sid] = {
                'label': new_src.get('label', sid),
                'url': new_src.get('url', ''),
                'added': added,
                'removed': removed,
            }
    return diff


def run_check(fetcher=fetch_text) -> dict:
    """Fetch every source, snapshot it and return {changed, diff, errors, seeded}."""
    old = load_snapshot()
    sources: dict = {}
    errors: dict = {}
    first_run = not old.get('sources')

    for source in PROVIDER_DOC_SOURCES:
        sid = source['id']
        try:
            text = html_to_text(fetcher(source['url']))
        except Exception as e:  # noqa: BLE001 - any network error must not abort
            errors[sid] = str(e)
            logger.warning('Provider docs fetch failed for %s: %s', sid, e)
            if old.get('sources', {}).get(sid):
                sources[sid] = old['sources'][sid]
            continue
        sources[sid] = snapshot_source(source, text)

    new = {
        'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'sources': sources,
    }
    diff = diff_snapshots(old, new)
    save_snapshot(new)
    return {
        'changed': bool(diff),
        'diff': diff,
        'errors': errors,
        'seeded': first_run,
    }


def format_change_email(diff: dict) -> str:
    parts = []
    for sid, change in sorted(diff.items()):
        lines = [f'=== {change["label"]} ({change["url"]}) ===']
        if change['added']:
            lines.append('Neu / geändert:')
            lines.extend(f'  + {r}' for r in change['added'])
        if change['removed']:
            lines.append('Entfernt:')
            lines.extend(f'  - {r}' for r in change['removed'])
        parts.append('\n'.join(lines))
    body = '\n\n'.join(parts)
    return (
        body
        + '\n\nBitte providers/opencode.py, providers/cline.py und '
          'AGENTS.md §3.12 prüfen und anpassen.'
    )
