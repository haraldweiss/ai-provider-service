#!/usr/bin/env python3
"""Host cron monitor. Read configuration in Docker; keep state and mail on host.

The --collect mode is streamed to the container's Python via stdin. It does not
install files or change the running service, routing, or provider caches.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
import json
import os
from pathlib import Path
import subprocess
import tempfile


def normalize_catalog(payload):
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows or payload.get('error'):
        raise ValueError('Invalid or empty catalog')
    result = {}
    for row in rows:
        mid = row.get('id') if isinstance(row, dict) else None
        if (not isinstance(mid, str) or not mid or len(mid) > 256
                or any(ord(c) < 32 for c in mid) or mid in result):
            raise ValueError('Invalid or duplicate model ID')
        pricing = row.get('pricing')
        if not isinstance(pricing, dict):
            raise ValueError('Missing pricing')
        for field in ('prompt', 'completion'):
            try:
                number = Decimal(str(pricing[field]))
            except (KeyError, InvalidOperation, TypeError, ValueError):
                raise ValueError('Invalid pricing') from None
            # OpenRouter uses -1 for dynamically priced router models.
            if not number.is_finite() or (number < 0 and number != -1):
                raise ValueError('Invalid pricing')
        architecture = row.get('architecture') or {}
        result[mid] = {
            'name': row.get('name'), 'context_length': row.get('context_length'),
            'pricing': pricing,
            'parameters': sorted(row.get('supported_parameters') or []),
            'input': sorted(architecture.get('input_modalities') or []),
            'output': sorted(architecture.get('output_modalities') or []),
        }
    return result


def catalog_diff(old, new):
    return {'added': sorted(new.keys() - old.keys()),
            'removed': sorted(old.keys() - new.keys()),
            'changed': sorted(mid for mid in old.keys() & new.keys() if old[mid] != new[mid])}


def completion_status(response):
    if response.status_code != 200:
        return 'http_%d' % response.status_code
    try:
        data = response.json()
        if data.get('error'):
            return 'upstream_error'
        choice = data['choices'][0]
        content = choice['message'].get('content')
        if not isinstance(content, str) or not content.strip():
            return 'empty_content'
        finish = choice.get('finish_reason')
        if finish != 'stop':
            return 'finish_length' if finish == 'length' else 'invalid_finish'
        return 'ok'
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return 'invalid_response'


def is_free_text(entry):
    if 'text' not in entry['output']:
        return False
    # Reject any extra scalar charge too (e.g. per-request pricing). Nested
    # pricing rules are not proven free and must never be used by this monitor.
    try:
        prices = [Decimal(str(v)) for v in entry['pricing'].values()]
        return bool(prices) and all(v.is_finite() and v == 0 for v in prices)
    except (InvalidOperation, TypeError, ValueError):
        return False


def check_models(catalog, model_ids, probe):
    def check(mid):
        if mid not in catalog:
            return mid, 'missing_from_catalog'
        if not is_free_text(catalog[mid]):
            return mid, 'paid_not_probed'
        if mid not in eligible:
            return mid, 'probe_budget_exceeded'
        status = probe(mid)
        if status != 'ok':
            status = probe(mid)  # Confirm a failure before alerting.
        return mid, status
    ids = sorted(set(model_ids))
    eligible = set(mid for mid in ids if mid in catalog and is_free_text(catalog[mid]))
    eligible = set(sorted(eligible)[:8])
    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(pool.map(check, ids))


def collect():
    # Imported only inside Docker: host Python needs the standard library only.
    import requests
    from sqlalchemy import create_engine, text
    from config import Config

    base = Config.OPENROUTER_BASE_URL.rstrip('/')
    headers = {'Authorization': 'Bearer ' + Config.OPENROUTER_API_KEY,
               'HTTP-Referer': 'https://ai-provider-service.wolfinisoftware.de',
               'X-OpenRouter-Title': 'ai-provider-service',
               'X-OpenRouter-Categories': 'ai-gateway'}
    response = requests.get(base + '/models', headers=headers, timeout=(5, 25))
    response.raise_for_status()
    catalog = normalize_catalog(response.json())
    ids = set()
    for spec in Config.MEMORY_FREE_MODELS:
        provider, _, mid = spec.partition('::')
        if provider == 'openrouter' and mid:
            ids.add(mid.removeprefix('openrouter/'))
    engine = create_engine(Config.SQLALCHEMY_DATABASE_URI)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT fallback_model FROM provider_configs "
                "WHERE fallback_provider='openrouter'"))
            for (mid,) in rows:
                if mid:
                    ids.add(mid.removeprefix('openrouter/'))
    finally:
        engine.dispose()

    def probe(mid):
        try:
            response = requests.post(base + '/chat/completions', headers=headers,
                                     json={'model': mid, 'max_tokens': 256,
                                           'messages': [{'role': 'user', 'content': 'Reply with exactly OK.'}]},
                                     timeout=(5, 35))
            return completion_status(response)
        except requests.RequestException:
            return 'connection_or_timeout'

    # Authenticated model list, without printing the service token or response.
    response = requests.get('http://127.0.0.1:8767/v1/models?user_id=' + Config.ADMIN_USER_ID,
                            headers={'Authorization': 'Bearer ' + Config.SERVICE_TOKEN},
                            timeout=(5, 60))
    response.raise_for_status()
    gateway_data = response.json()['data']
    gateway = sorted(row['id'].removeprefix('openrouter/') for row in gateway_data
                     if row['id'].startswith('openrouter/'))
    return {'models': catalog, 'probes': check_models(catalog, ids, probe),
            'gateway_stale': sorted(set(gateway) - catalog.keys()),
            'gateway_missing_configured': sorted(mid for mid in ids
                                                 if mid in catalog and is_free_text(catalog[mid])
                                                 and mid not in gateway),
            # A free-only admin or grant can intentionally hide paid models.
            # Only disappearance from the upstream catalog is definitely stale.
            'gateway_openrouter_count': len(gateway)}


def collect_from_container(container):
    result = subprocess.run(['docker', 'exec', '-i', container, 'python', '-', '--collect'],
                            input=Path(__file__).read_text(), text=True,
                            capture_output=True, timeout=480, check=True)
    return json.loads(result.stdout)


def save_snapshot(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.openrouter-', dir=path.parent)
    try:
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
    message['Subject'] = 'ai-provider: OpenRouter model changes / availability'
    message['From'] = 'ai-provider@wolfinisoftware.de'
    message['To'] = recipient
    message.set_content(body)
    subprocess.run(['/usr/sbin/sendmail', '-t'], input=message.as_bytes(),
                   capture_output=True, timeout=15, check=True)


def run_check(path, collector, notify):
    try:
        old = json.loads(path.read_text()) if path.exists() else None
        current = collector()
        if not isinstance(current.get('models'), dict) or not current['models']:
            raise ValueError('Invalid collection result')
        diff = catalog_diff(old['models'], current['models']) if old else {}
        failures = {mid: status for mid, status in current['probes'].items() if status != 'ok'}
        recovered = sorted(mid for mid, status in (old or {}).get('probes', {}).items()
                           if status != 'ok' and current['probes'].get(mid) == 'ok')
        stale = current.get('gateway_stale', [])
        missing = current.get('gateway_missing_configured', [])
        lines = []
        for kind, ids in diff.items():
            if ids:
                lines.append(kind + ':\n' + '\n'.join(ids))
        if failures:
            lines.append('Configured model problems:\n' + '\n'.join(
                mid + ': ' + status for mid, status in sorted(failures.items())))
        if recovered:
            lines.append('Recovered:\n' + '\n'.join(recovered))
        if stale:
            lines.append('Stale gateway IDs:\n' + '\n'.join(stale))
        if missing:
            lines.append('Missing configured gateway IDs:\n' + '\n'.join(missing))
        if lines:
            notify('OpenRouter daily check (UTC): ' + datetime.now(timezone.utc).isoformat()
                   + '\n\n' + '\n\n'.join(lines)
                   + '\n\nMonitoring only; no routing or production configuration changed.')
        current['checked_at'] = datetime.now(timezone.utc).isoformat()
        save_snapshot(path, current)
        print('OpenRouter: %d models, %d configured probes, %d problems; snapshot saved'
              % (len(current['models']), len(current['probes']), len(failures) + len(stale) + len(missing)))
        return 1 if failures or stale or missing else 0
    except Exception as error:
        # Never print subprocess stdout/stderr, request exceptions, or raw
        # provider errors: they may contain credentials or upstream content.
        message = 'OpenRouter daily check failed (%s); previous snapshot preserved.' % type(error).__name__
        print(message)
        try:
            notify(message)
        except Exception:
            print('Notification delivery failed; check host mail transport.')
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--container', default='ai-provider')
    parser.add_argument('--snapshot', type=Path, default=Path('/var/lib/ai-provider-monitor/openrouter.json'))
    parser.add_argument('--recipient', default='harald.weiss@wolfinisoftware.de')
    args = parser.parse_args()
    if args.collect:
        try:
            print(json.dumps(collect()))
            return 0
        except Exception as error:
            print('Collection failed (%s)' % type(error).__name__, file=__import__('sys').stderr)
            return 1
    return run_check(args.snapshot, lambda: collect_from_container(args.container),
                     lambda body: send_notification(body, args.recipient))


if __name__ == '__main__':
    raise SystemExit(main())
