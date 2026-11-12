#!/usr/bin/env python3
"""Host cron documentation monitor: public docs in Docker, state and mail on host."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

# Public documentation only; no user-configured inference URLs or provider keys.
SOURCES = [
    {'id': 'opencode-go', 'url': 'https://opencode.ai/docs/console/go/',
     'format': 'html', 'anchor': 'x-opencode-session'},
    {'id': 'opencode-zen', 'url': 'https://opencode.ai/docs/console/models/',
     'format': 'html', 'anchor': 'pricing', 'pricing': True},
    {'id': 'opencode-inference', 'url': 'https://opencode.ai/docs/console/api/inference/',
     'format': 'html', 'anchor': '/inference/openai/v1'},
    {'id': 'cline-auth', 'url': 'https://docs.cline.bot/api/authentication.md',
     'format': 'markdown', 'anchor': 'authorization'},
    {'id': 'cline-models', 'url': 'https://docs.cline.bot/api/models.md',
     'format': 'markdown', 'anchor': 'provider/model-name'},
    {'id': 'openai', 'url': 'https://developers.openai.com/api/reference/overview.md',
     'format': 'markdown', 'anchor': 'authorization'},
    {'id': 'claude', 'url': 'https://platform.claude.com/docs/en/api/overview.md',
     'format': 'markdown', 'anchor': 'anthropic-version'},
    {'id': 'openrouter-auth', 'url': 'https://openrouter.ai/docs/api_reference/authentication.md',
     'format': 'markdown', 'anchor': 'bearer'},
    {'id': 'openrouter-client', 'url': 'https://openrouter.ai/docs/quickstart.md',
     'format': 'markdown', 'anchor': 'openrouter.ai/api/v1'},
    {'id': 'zai', 'url': 'https://docs.z.ai/api-reference/introduction.md',
     'format': 'markdown', 'anchor': 'api.z.ai/api/paas/v4'},
    {'id': 'ollama-api', 'url': 'https://docs.ollama.com/api/introduction.md',
     'format': 'markdown', 'anchor': '11434/api'},
    {'id': 'ollama-auth', 'url': 'https://docs.ollama.com/api/authentication.md',
     'format': 'markdown', 'anchor': 'authentication'},
    {'id': 'omlx', 'url': 'https://raw.githubusercontent.com/jundot/omlx/main/README.md',
     'format': 'markdown', 'anchor': '/v1/chat/completions'},
]
WATCH_TERMS = (
    'user-agent', 'user agent', 'x-opencode-', 'http-referer', 'x-title',
    'x-openrouter-', 'x-task-id', 'session', 'free-tier', 'free tier',
    'free model', 'authorization', 'authentication', 'api key', 'api_key',
    'x-api-key', 'x-client-request-id', 'header', 'anthropic-version', 'required', 'deprecated', 'breaking',
    'rate limit', 'base url', 'endpoint', '/v1/', '/api/', 'model id',
)
MAX_BODY_BYTES = 2 * 1024 * 1024


def fetch_document(url):
    request = urllib.request.Request(
        url, headers={'User-Agent': 'ai-provider-service/provider-docs-watch'},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(MAX_BODY_BYTES + 1)
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError('Document exceeds size limit')
    return raw.decode('utf-8', errors='replace')


def markdown_blocks(markup):
    """Join soft-wrapped prose; retain fenced examples and whole tables."""
    blocks, lines, fence = [], [], None

    def flush():
        if lines:
            blocks.append(' '.join(' '.join(lines).split()))
            lines.clear()

    for line in markup.splitlines():
        marker = re.match(r'^\s*([\x60]{3,}|~{3,})', line)
        if marker:
            character = marker.group(1)[0]
            if fence is None:
                flush()
                fence = character
            elif character == fence:
                lines.append(line)
                flush()
                fence = None
                continue
        if line.strip() or fence:
            lines.append(line)
        else:
            flush()
    flush()
    return blocks


def collect(fetcher=None, sources=None):
    """Fetch public docs concurrently; never invoke inference or load keys."""
    from provider_docs import html_to_text

    fetcher = fetcher or fetch_document
    sources = SOURCES if sources is None else sources

    def one(source):
        try:
            for attempt in range(2):
                try:
                    raw = fetcher(source['url'])
                    break
                except Exception:
                    if attempt:
                        raise
            if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_BODY_BYTES:
                return source['id'], {'rules': [], 'error': 'invalid document size/type'}
            blocks = (html_to_text(raw).splitlines() if source['format'] == 'html'
                      else markdown_blocks(raw))
            if source['anchor'].lower() not in '\n'.join(blocks).lower():
                return source['id'], {'rules': [], 'error': 'required API text missing'}
            rules = sorted(set(
                re.sub(r'\s+', ' ', line).strip()
                for line in blocks
                if line.strip() and any(term in line.lower() for term in WATCH_TERMS)
            ))
            if not rules:
                return source['id'], {'rules': [], 'error': 'no rule text extracted'}
            if source.get('pricing'):
                from cli import _parse_opencode_pricing
                _parse_opencode_pricing(raw)
            return source['id'], {'rules': rules, 'error': None}
        except Exception as error:
            # Never include response bodies or exception text in diagnostics.
            status = getattr(error, 'code', None)
            reason = type(error).__name__
            if isinstance(status, int):
                reason += ' HTTP %d' % status
            return source['id'], {'rules': [], 'error': reason}

    with ThreadPoolExecutor(max_workers=4) as pool:
        return {'sources': dict(pool.map(one, sources))}


def collect_from_container(container):
    result = subprocess.run(
        ['docker', 'exec', '-i', container, 'python', '-', '--collect'],
        input=Path(__file__).read_text(), text=True, capture_output=True,
        timeout=360, check=True,
    )
    if len(result.stdout.encode('utf-8')) > MAX_BODY_BYTES:
        raise ValueError('Collector output too large')
    return json.loads(result.stdout)


def validate_report(report, source_ids):
    if not isinstance(report, dict) or not isinstance(report.get('sources'), dict):
        raise ValueError('Invalid report')
    if set(report['sources']) != source_ids:
        raise ValueError('Missing or unexpected sources')
    for item in report['sources'].values():
        if not isinstance(item, dict) or set(item) != {'rules', 'error'}:
            raise ValueError('Invalid source')
        rules, error = item['rules'], item['error']
        if not isinstance(rules, list) or not all(isinstance(r, str) and r.strip() for r in rules):
            raise ValueError('Invalid rules')
        if error is not None and (not isinstance(error, str) or not error):
            raise ValueError('Invalid error')
        if error is None and not rules:
            raise ValueError('Empty healthy rules')


def save_snapshot(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def send_notification(body, recipient):
    message = EmailMessage()
    message['Subject'] = 'ai-provider: documentation changes / monitor status'
    message['From'] = 'ai-provider@wolfinisoftware.de'
    message['To'] = recipient
    message.set_content(body)
    subprocess.run(['/usr/sbin/sendmail', '-t'], input=message.as_bytes(),
                   capture_output=True, timeout=15, check=True)


def run_check(path, collector, notify, source_ids=None):
    source_ids = {source['id'] for source in SOURCES} if source_ids is None else source_ids
    try:
        old = json.loads(path.read_text()) if path.exists() else {'sources': {}}
        if not isinstance(old, dict) or not isinstance(old.get('sources'), dict):
            raise ValueError('Invalid snapshot')
        validate_report({'sources': old['sources']}, set(old['sources']))
    except Exception as error:
        print('Documentation snapshot unreadable (%s); preserved.' % type(error).__name__)
        try:
            notify('Documentation monitor snapshot unreadable; inspect host state. '
                   'No baseline was replaced.')
        except Exception:
            print('Host mail submission failed.')
        return 1

    lines = []
    new = {'sources': {}, 'collector_error': None,
           'checked_at': datetime.now(timezone.utc).isoformat()}
    try:
        current = collector()
        validate_report(current, source_ids)
    except Exception as error:
        new['sources'] = old['sources']
        new['collector_error'] = type(error).__name__
        if new['collector_error'] != old.get('collector_error'):
            lines.append('Documentation collector failed (%s); last good rules retained.'
                         % new['collector_error'])
    else:
        if old.get('collector_error'):
            lines.append('Recovered: documentation collector.')
        for sid, entry in sorted(current['sources'].items()):
            prior = old['sources'].get(sid, {})
            error = entry['error']
            if error:
                new['sources'][sid] = {'rules': prior.get('rules', []), 'error': error}
                if error != prior.get('error'):
                    lines.append('Problem: %s: %s' % (sid, error))
                continue
            new['sources'][sid] = entry
            if prior.get('error'):
                lines.append('Recovered: %s.' % sid)
            previous_rules = prior.get('rules', [])
            if previous_rules:
                added = sorted(set(entry['rules']) - set(previous_rules))
                removed = sorted(set(previous_rules) - set(entry['rules']))
                if added or removed:
                    lines.append('Changed: %s (+%d / -%d)' % (sid, len(added), len(removed)))
                    for prefix, rules in (('+ ', added), ('- ', removed)):
                        lines.extend(prefix + rule[:600] for rule in rules[:20])
                        if len(rules) > 20:
                            lines.append('... %d further lines; inspect snapshot/docs.' % (len(rules) - 20))

    problems = bool(new['collector_error']) or any(
        source.get('error') for source in new['sources'].values()
    )
    try:
        if lines:
            urls = '\n'.join(source['id'] + ': ' + source['url'] for source in SOURCES)
            notify('Provider documentation monitor (UTC): ' + new['checked_at']
                   + '\n\n' + '\n'.join(lines) + '\n\nSources:\n' + urls
                   + '\n\nReview provider clients and run tests before deploying fixes. '
                   'Monitoring does not change provider settings or guarantee availability.')
        # A failed mail submission must leave the notification pending for retry.
        save_snapshot(path, new)
    except Exception as error:
        print('Documentation state/notification failed (%s); previous snapshot retained.'
              % type(error).__name__)
        return 1
    print('Provider docs: %d sources; status=%s; notifications=%d; snapshot saved'
          % (len(new['sources']), 'problem' if problems else 'ok', bool(lines)))
    return 1 if problems else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--container', default='ai-provider')
    parser.add_argument('--snapshot', type=Path,
                        default=Path('/var/lib/ai-provider-monitor/provider-docs.json'))
    parser.add_argument('--recipient', default='harald.weiss@wolfinisoftware.de')
    args = parser.parse_args()
    if args.collect:
        print(json.dumps(collect()))
        return 0
    return run_check(args.snapshot, lambda: collect_from_container(args.container),
                     lambda body: send_notification(body, args.recipient))


if __name__ == '__main__':
    sys.exit(main())
