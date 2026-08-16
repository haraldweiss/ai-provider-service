"""Flask CLI commands for ai-provider-service.

Register with: app.cli.add_command(...) in app.create_app().

Commands:
  grants-bootstrap: insert one active grant per existing (user_id, provider_id)
    in provider_configs where provider_id is NOT in Config.UNGATED_PROVIDERS.
    Idempotent.
  update-opencode-pricing: fetch opencode.ai Zen rate card and persist as JSON.
"""

from __future__ import annotations
import json
import os
import re
import urllib.request
from pathlib import Path
import click
from datetime import datetime, timezone
from database import db
from config import Config
from storage.models import ProviderConfig, ProviderGrant


def bootstrap_grants() -> int:
    """Returns number of new grants created."""
    ungated = Config.UNGATED_PROVIDERS
    rows = ProviderConfig.query.filter(
        ~ProviderConfig.provider_id.in_(ungated)
    ).all()
    created = 0
    for cfg in rows:
        existing = ProviderGrant.query.filter_by(
            user_id=cfg.user_id, provider_id=cfg.provider_id
        ).first()
        if existing:
            continue
        db.session.add(ProviderGrant(
            user_id=cfg.user_id,
            provider_id=cfg.provider_id,
            granted_by='bootstrap',
            note='bootstrap from existing provider_configs',
        ))
        created += 1
    db.session.commit()
    return created


@click.command('grants-bootstrap')
def grants_bootstrap_command():
    """Insert grants for existing provider_configs (one-shot, idempotent)."""
    n = bootstrap_grants()
    click.echo(f'Created {n} new grants.')


OPencode_PRICING_URL = 'https://opencode.ai/docs/zen/'


def _parse_opencode_pricing(html: str) -> dict[str, dict[str, float]]:
    """Parse the Zen pricing table from opencode.ai docs HTML.

    Returns dict keyed by 'opencode::{model_id}' with {'in': X, 'out': Y}.
    """
    models_list = {}
    # Find the pricing table: the table whose header contains Input/Output columns.
    # There are multiple tables in the Pricing section; the first is the endpoints
    # table (Model ID, Endpoint, SDK Package). We want the second one (pricing).
    table_match = re.search(
        r'<table[^>]*>(?:(?!</table>).)*?<th[^>]*>Input</th>.*?<th[^>]*>Output</th>.*?</table>',
        html, re.DOTALL | re.IGNORECASE
    )
    if not table_match:
        raise ValueError('Pricing table not found in opencode.ai Zen docs')

    table_html = table_match.group(0)
    rows = re.findall(
        r'<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>'
        r'(?:Free|\$?([\d.]+))</td>\s*<td[^>]*>'
        r'(?:Free|\$?([\d.]+))</td>',
        table_html, re.DOTALL
    )

    for match in rows:
        model_name = match[0].strip()
        inp_str = match[1] if match[1] else '0.0'
        out_str = match[2] if match[2] else '0.0'
        model_id = _model_name_to_id(model_name)
        inp = float(inp_str)
        out = float(out_str)
        models_list[f'opencode::{model_id}'] = {'in': inp, 'out': out}

    # Ensure free models (re.findall may not match "Free" with $)
    free_ids = [
        'big-pickle', 'deepseek-v4-flash-free', 'mimo-v2.5-free',
        'nemotron-3-super-free', 'qwen3.6-plus-free', 'minimax-m2.5-free',
    ]
    for fid in free_ids:
        key = f'opencode::{fid}'
        if key not in models_list:
            models_list[key] = {'in': 0.0, 'out': 0.0}

    return models_list


def _model_name_to_id(name: str) -> str:
    """Convert display name like 'GPT 5.4 Mini' to model id 'gpt-5.4-mini'."""
    # Handle context-length variants: keep only the base name
    name = re.sub(r'\s*\([^)]*\)\s*', '', name)
    name = name.strip().lower()
    name = re.sub(r'[^\w\s.-]', '', name)
    name = re.sub(r'\s+', '-', name)
    name = re.sub(r'-+', '-', name)
    # Specific overrides for names that don't match model IDs
    overrides = {
        'qwen3.7-max': 'qwen3.7-max',
        'claude-haiku-3.5': 'claude-3-5-haiku',
    }
    return overrides.get(name, name)


def fetch_opencode_pricing() -> dict[str, dict[str, float]]:
    """Fetch and parse the opencode.ai Zen pricing page."""
    req = urllib.request.Request(
        OPencode_PRICING_URL,
        headers={'User-Agent': 'ai-provider-service/1.0 (pricing sync)'},
    )
    resp = urllib.request.urlopen(req, timeout=15)
    html = resp.read().decode('utf-8')
    return _parse_opencode_pricing(html)


def save_opencode_pricing(data: dict[str, dict[str, float]]) -> Path:
    """Persist pricing data to pricing_overrides.json next to pricing.py."""
    path = Path(__file__).parent / 'pricing_overrides.json'
    path.write_text(json.dumps(data, indent=2) + '\n')
    return path


@click.command('update-opencode-pricing')
def update_opencode_pricing_command():
    """Fetch opencode.ai Zen rate card and persist as JSON override."""
    try:
        click.echo('Fetching opencode.ai Zen pricing ...')
        data = fetch_opencode_pricing()
        path = save_opencode_pricing(data)
        click.echo(f'{len(data)} models written to {path}')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        raise click.Abort()


# --- z.ai (GLM) tariff sync ------------------------------------------------

ZAI_PRICING_URL = 'https://docs.z.ai/guides/overview/pricing.md'
ZAI_NOTIFY_EMAIL = 'harald.weiss@wolfinisoftware.de'


def _split_md_row(line: str) -> list[str]:
    """Splits a markdown table row '| a | b |' into trimmed cells."""
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _is_md_separator(line: str) -> bool:
    return bool(re.match(r'^\s*\|?[\s:|-]+\|?\s*$', line)) and '-' in line


def _parse_zai_price_cell(val: str):
    """Parse a price cell. 'Free' → 0.0; '\\$1.4' → 1.4; '-'/'\\\\'/'' → None."""
    v = val.strip()
    if v.lower() == 'free':
        return 0.0
    v = v.replace('\\', '').replace('$', '').strip()
    if not v or v == '-':
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _parse_zai_pricing(md: str) -> dict[str, dict[str, float]]:
    """Parse the z.ai pricing markdown into {'zai::<model-id>': {in, out}}.

    Only tables whose header has both an 'Input' and an 'Output' column are
    token-priced (Text + Vision models); other tables (tools, image, video)
    are ignored. Model display names map to lowercased API ids.
    """
    result: dict[str, dict[str, float]] = {}
    in_idx = out_idx = None
    for line in md.splitlines():
        if '|' not in line:
            in_idx = out_idx = None
            continue
        if _is_md_separator(line):
            continue
        cells = _split_md_row(line)
        lowered = [c.lower() for c in cells]
        if 'input' in lowered and 'output' in lowered:
            in_idx = lowered.index('input')
            out_idx = lowered.index('output')
            continue
        if in_idx is None or len(cells) <= max(in_idx, out_idx):
            continue
        model = cells[0]
        if not model or model.lower() == 'model':
            continue
        pin = _parse_zai_price_cell(cells[in_idx])
        pout = _parse_zai_price_cell(cells[out_idx])
        if pin is None or pout is None:
            continue
        result[f'zai::{model.lower()}'] = {'in': pin, 'out': pout}
    return result


def fetch_zai_pricing() -> dict[str, dict[str, float]]:
    """Fetch and parse the z.ai pricing markdown page."""
    req = urllib.request.Request(
        ZAI_PRICING_URL,
        headers={'User-Agent': 'ai-provider-service/1.0 (pricing sync)'},
    )
    resp = urllib.request.urlopen(req, timeout=15)
    md = resp.read().decode('utf-8')
    data = _parse_zai_pricing(md)
    if not data:
        raise ValueError('No GLM models parsed from z.ai pricing page')
    return data


def load_existing_zai_pricing() -> dict[str, dict[str, float]]:
    import pricing
    path = pricing._ZAI_OVERRIDE_PATH
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_zai_pricing(data: dict[str, dict[str, float]]) -> Path:
    """Persist z.ai pricing to its own override file (separate from opencode)."""
    import pricing
    path = pricing._ZAI_OVERRIDE_PATH
    path.write_text(json.dumps(data, indent=2) + '\n')
    return path


def _diff_pricing(old: dict, new: dict) -> dict:
    """Returns {'added', 'removed', 'changed'} between two pricing dicts."""
    added = {k: new[k] for k in new if k not in old}
    removed = {k: old[k] for k in old if k not in new}
    changed = {
        k: (old[k], new[k]) for k in new if k in old and new[k] != old[k]
    }
    return {'added': added, 'removed': removed, 'changed': changed}


def _format_zai_change_email(diff: dict) -> str:
    """Human-readable diff body for the tariff-change notification."""
    def _fmt(rates: dict) -> str:
        return f"in \\${rates['in']}/Mtok, out \\${rates['out']}/Mtok"

    parts = []
    if diff['added']:
        parts.append('Neue Modelle / Tarife:\n' + '\n'.join(
            f'  + {k.split("::", 1)[1]} ({_fmt(v)})'
            for k, v in sorted(diff['added'].items())))
    if diff['removed']:
        parts.append('Nicht mehr gelistet:\n' + '\n'.join(
            f'  - {k.split("::", 1)[1]}' for k in sorted(diff['removed'])))
    if diff['changed']:
        parts.append('Preisänderungen:\n' + '\n'.join(
            f'  ~ {k.split("::", 1)[1]}: {_fmt(o)} → {_fmt(n)}'
            for k, (o, n) in sorted(diff['changed'].items())))
    return '\n\n'.join(parts)


def _send_email(subject: str, body: str, to: str = ZAI_NOTIFY_EMAIL) -> None:
    import subprocess
    import logging
    _log = logging.getLogger(__name__)
    try:
        msg = (f'Subject: {subject}\nFrom: ai-provider@wolfinisoftware.de\n'
               f'To: {to}\n\n{body}\n')
        subprocess.run(['/usr/sbin/sendmail', '-t'], input=msg,
                       capture_output=True, timeout=10, text=True)
    except (subprocess.TimeoutExpired, OSError) as e:
        _log.warning('Failed to send tariff-change email: %s', e)
    except Exception as e:
        _log.warning('Unexpected error sending tariff-change email: %s', e)


@click.command('update-zai-pricing')
def update_zai_pricing_command():
    """Fetch z.ai (GLM) rate card, persist it, and email the owner on change."""
    try:
        click.echo('Fetching z.ai pricing ...')
        new = fetch_zai_pricing()
        old = load_existing_zai_pricing()
        diff = _diff_pricing(old, new)
        path = save_zai_pricing(new)
        click.echo(f'{len(new)} models written to {path}')
        if old and (diff['added'] or diff['removed'] or diff['changed']):
            _send_email('z.ai: Tarif-Änderungen erkannt',
                        _format_zai_change_email(diff)
                        + f'\n\nQuelle: {ZAI_PRICING_URL}')
            click.echo('Tariff change detected — notification sent.')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        raise click.Abort()


# --- Cline (api.cline.bot) model catalog sync ---------------------------------
#
# Cline exposes no public /models endpoint (GET /models returns 401 while auth-
# walled), so the served model list + pricing in pricing_overrides_cline.json is
# curated from Cline's OSS model catalog:
#
#   sdk/packages/llms/src/catalog/catalog.generated.ts   (cline/cline, branch main)
#
# The generated file is:
#
#   export const GENERATED_PROVIDER_MODELS: { version: N, providers: {...} } = {
#     version: <unix_ms>,
#     providers: {
#       "<provider_key>": { "<model_id>": { ..., "pricing": {"input": X, "output": Y} } },
#       ...
#     }
#   };
#
# CAUTION (do not auto-overwrite): the catalog keys are Cline's *internal*
# provider keys (e.g. `alibaba`, `minimax`, `deepseek`) while api.cline.bot
# serves model IDs under different display prefixes (e.g. `Qwen/...`,
# `MiniMaxAI/...`, `JetBrains/...`). Only ~11% of the override's served IDs
# have an exact provider/model counterpart in the catalog. Therefore this
# command is REPORT-FIRST: by default it fetches + parses the catalog and
# prints (a) the ClinePass dedup candidates (non-pass duplicates that should be
# dropped because ClinePass covers them for free) and (b) price/availability
# changes, WITHOUT writing anything. Pass `--apply` to actually rewrite the
# committed pricing_overrides_cline.json after human review.
CLINE_CATALOG_URL = ('https://raw.githubusercontent.com/cline/cline/main/'
                     'sdk/packages/llms/src/catalog/catalog.generated.ts')


def _extract_catalog_providers(ts: str) -> dict:
    """Parse the ``providers`` record out of catalog.generated.ts (pure Python).

    Only the two top-level keys (``version``/``providers``) are unquoted; every
    model/pricing key below is strict JSON (double-quoted, no trailing commas).
    We slice from the ``{`` right after ``providers:`` and use raw_decode so we
    stop at the end of that exact object. Node.js is NOT required, so this runs
    unchanged inside the ai-provider container.
    """
    start = ts.index('= {') + 2          # start of the assigned object literal
    rel = ts[start:]
    vidx = rel.index('providers:')
    lbrace = rel.index('{', vidx)
    providers, _ = json.JSONDecoder().raw_decode(rel[lbrace:])
    return providers


def _catalog_pricing(providers: dict) -> dict:
    """Flatten the catalog to {provider_key/model_id(.lower()): (in, out)}.

    Models without an input/output pair are skipped (no per-token rate).
    """
    result = {}
    for prov_key, models in providers.items():
        for model_id, info in models.items():
            pricing = info.get('pricing') or {}
            pin = pricing.get('input')
            pout = pricing.get('output')
            if pin is None or pout is None:
                continue
            result[f'{prov_key}/{model_id}'.lower()] = (pin, pout)
    return result


def _is_free_entry(served_id: str, rates: dict) -> bool:
    """A served model is treated as free if its rate is $0 or it carries :free."""
    return (':free' in served_id.lower()
            or (rates.get('in') == 0.0 and rates.get('out') == 0.0))


def _clinepass_drop_candidates(override: dict) -> list[str]:
    """Return the override's `cline::` keys that should be dropped because the
    same open-weight model is covered by ClinePass (free) under `cline-pass/`.

    Rule (ClinePass holder): keep the `cline-pass/<model>` versions and drop the
    equivalent non-pass versions — EXCEPT keep the free (`:free`/$0) ones, which
    the user explicitly wants retained. Matches on the trailing model segment
    (case-insensitive), like the existing override's redundancy between e.g.
    `zai/glm-5.2` and `cline-pass/glm-5.2`.
    """
    covered = override.get('_meta', {}).get('clinepass_models') or []
    covered_tails = {m.split('/')[-1].lower() for m in covered}
    dropped = []
    for key, rates in override.items():
        if not key.startswith('cline::'):
            continue
        served = key[len('cline::'):]
        if served.startswith('cline-pass/'):
            continue  # the covered variant itself stays
        tail = served.split('/')[-1].lower()
        if tail in covered_tails and not _is_free_entry(served, rates):
            dropped.append(key)
    return sorted(dropped)


def _price_changes(override: dict, catalog: dict) -> dict:
    """Map override served ID -> (old, new) for IDs with an exact catalog match
    whose rate differs. Served IDs are matched case-insensitively against the
    catalog's provider/model key."""
    changes = {}
    for key, rates in override.items():
        if not key.startswith('cline::'):
            continue
        served = key[len('cline::'):]
        hit = catalog.get(served.lower())
        if hit is None:
            continue
        cin, cout = hit
        if rates.get('in') != cin or rates.get('out') != cout:
            changes[key] = ({'in': rates.get('in'), 'out': rates.get('out')},
                            {'in': cin, 'out': cout})
    return changes


def load_existing_cline_pricing() -> dict:
    """Read the committed pricing_overrides_cline.json (or {} if missing)."""
    import pricing
    path = pricing._CLINE_OVERRIDE_PATH
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_cline_pricing(data: dict) -> Path:
    """Persist the Cline override file (used only by --apply)."""
    import pricing
    path = pricing._CLINE_OVERRIDE_PATH
    path.write_text(json.dumps(data, indent=2) + '\n')
    return path


def _refresh_cline_meta(data: dict, applied_ids: list[str]) -> dict:
    """Update the _meta block (counts + timestamp + applied note). In-place."""
    meta = dict(data.get('_meta', {}))
    model_keys = [k for k in data if k.startswith('cline::')]
    meta['last_updated'] = datetime.now(timezone.utc).isoformat(timespec='minutes')
    meta['total_models'] = len(model_keys)
    meta['clinepass_count'] = sum(
        1 for k in model_keys if k[len('cline::'):].startswith('cline-pass/'))
    meta['free_count'] = sum(
        1 for k in model_keys if _is_free_entry(k[len('cline::'):], data.get(k, {})))
    if applied_ids:
        meta['last_apply'] = {
            'at': datetime.now(timezone.utc).isoformat(timespec='minutes'),
            'dropped_pass_dups': list(applied_ids),
        }
    data['_meta'] = meta
    return data


def apply_clinepass_and_prices(override: dict, catalog: dict) -> tuple[dict, list[str], dict]:
    """Build the rewritten override for --apply.

    Drops the ClinePass non-pass duplicates and applies catalog price updates
    for exactly-matched, NOT-dropped served IDs. The file stays curated (no
    wholesale catalog import). Returns (updated_file, dropped_keys,
    price_changes)."""
    dropped = _clinepass_drop_candidates(override)
    dropped_set = set(dropped)
    changes = {k: v for k, v in _price_changes(override, catalog).items()
               if k not in dropped_set}
    new_data = dict(override)
    for key in dropped:
        new_data.pop(key, None)
    for key, (_old, new) in changes.items():
        new_data[key] = {'in': new['in'], 'out': new['out']}
    return _refresh_cline_meta(new_data, dropped), dropped, changes


@click.command('update-cline-catalog')
@click.option('--apply', is_flag=True,
              help='Rewrite pricing_overrides_cline.json after review (drops '
                   'non-pass ClinePass duplicates, applies matched price updates).')
def update_cline_catalog_command(apply):
    """Fetch Cline's OSS model catalog, diff against the committed override and
    report for review (no write unless --apply)."""
    try:
        click.echo('Fetching Cline model catalog ...')
        req = urllib.request.Request(
            CLINE_CATALOG_URL,
            headers={'User-Agent': 'ai-provider-service/1.0 (cline catalog sync)'},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        ts = resp.read().decode('utf-8')

        providers = _extract_catalog_providers(ts)
        catalog = _catalog_pricing(providers)
        override = load_existing_cline_pricing()

        n_providers = len(providers)
        n_catalog = len(catalog)
        n_served = sum(1 for k in override if k.startswith('cline::'))
        click.echo(f'Catalog: {n_providers} providers, {n_catalog} priced models')
        click.echo(f'Committed override: {n_served} served model IDs\n')

        dropped = _clinepass_drop_candidates(override)
        if dropped:
            click.echo('[ClinePass dedup] keep `cline-pass/*` and drop these '
                       f'{len(dropped)} paid non-pass duplicates (free kept):')
            for k in dropped:
                click.echo(f'  - {k[len("cline::"):]}')
        else:
            click.echo('[ClinePass dedup] no non-pass duplicates to drop.\n')

        changes = _price_changes(override, catalog)
        if changes:
            click.echo(f'[Prices] {len(changes)} served IDs have an exact catalog '
                       'match with a different rate (review before applying):')
            for k, (old, new) in sorted(changes.items()):
                click.echo(f'  ~ {k[len("cline::"):]}: '
                           f'in {old["in"]}/out {old["out"]} -> '
                           f'in {new["in"]}/out {new["out"]}')
        else:
            click.echo('[Prices] no exact-match price differences found.')

        known = {k[len('cline::'):].lower() for k in override
                 if k.startswith('cline::')}
        new_ids = sorted(m for m in catalog if m not in known)
        click.echo(f'\n[New] {len(new_ids)} catalog model IDs not in the override '
                   '(catalog-key only; display-prefix mapping needs human review '
                   'before adding to /v1/models):')
        for m in new_ids[:20]:
            click.echo(f'  + {m}')
        if len(new_ids) > 20:
            click.echo(f'  ... and {len(new_ids) - 20} more')

        if apply:
            import pricing
            new_file, dropped_applied, applied_changes = apply_clinepass_and_prices(
                override, catalog)
            path = save_cline_pricing(new_file)
            pricing._reset_pricing_cache()
            click.echo(f'\nApplied: {len(dropped_applied)} dups dropped, '
                       f'{len(applied_changes)} price updates -> {path}')
        else:
            click.echo('\nReport only — pass --apply to write. Nothing changed.')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        raise click.Abort()


# --- Daily Cline model-change notification -----------------------------------
#
# Mirrors the opencode free-model refresh pattern: keep a small snapshot of the
# last-seen curated served model list, diff it against the current committed
# override and email the owner on any add/remove/change. The snapshot lives next
# to the override file (survives container rebuilds) and is rotated to `.prev`
# just like providers/opencode.py does for the free-model cache, so a regen can
# always recover the previous state.
CLINE_NOTIFY_EMAIL = 'harald.weiss@wolfinisoftware.de'
CLINE_SNAPSHOT_PATH = None  # set lazily from pricing._CLINE_OVERRIDE_PATH

def _cline_snapshot_path() -> Path:
    """Path of the Cline served-model snapshot (next to the override file)."""
    global CLINE_SNAPSHOT_PATH
    if CLINE_SNAPSHOT_PATH is None:
        import pricing
        CLINE_SNAPSHOT_PATH = pricing._CLINE_OVERRIDE_PATH.with_suffix(
            pricing._CLINE_OVERRIDE_PATH.suffix + '.snapshot')
    return Path(CLINE_SNAPSHOT_PATH)


def load_cline_snapshot() -> dict:
    """Load the last-seen curated served-model snapshot ({} if none)."""
    path = _cline_snapshot_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if 'served' in data:
                return data
        except Exception:
            return {}
    return {}


def save_cline_snapshot(served: dict) -> Path:
    """Write the current curated served list as the new snapshot (rotate .prev)."""
    path = _cline_snapshot_path()
    if path.exists():
        try:
            os.replace(path, path.with_suffix(path.suffix + '.prev'))
        except OSError:
            pass
    payload = {
        'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'served': {k: v for k, v in served.items()
                   if k.startswith('cline::') and isinstance(v, dict)},
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def diff_cline_served(old: dict, new: dict) -> dict:
    """Compare two curated served lists -> added/removed/changed keys (full IDs)."""
    o = {k: v for k, v in old.get('served', {}).items()}
    n = {k: v for k, v in new.get('served', {}).items()}
    added = {k: n[k] for k in n if k not in o}
    removed = {k: o[k] for k in o if k not in n}
    changed = {k: (o[k], n[k]) for k in n if k in o and n[k] != o[k]}
    return {'added': added, 'removed': removed, 'changed': changed}


def _format_cline_change_email(diff: dict) -> str:
    """Human-readable diff for the model-change notification email."""
    parts = []
    if diff['added']:
        parts.append('Neue Modelle im Cline-Katalog gelistet:\n' + '\n'.join(
            f'  + {k[len("cline::"):]}' for k in sorted(diff['added'])))
    if diff['removed']:
        parts.append('Nicht mehr gelistet (entfernt):\n' + '\n'.join(
            f'  - {k[len("cline::"):]}' for k in sorted(diff['removed'])))
    if diff['changed']:
        def _fmt(rates):
            if isinstance(rates, dict):
                return f'in ${rates.get("in")}/Mtok, out ${rates.get("out")}/Mtok'
            try:
                i, o = rates
                return f'in ${i}/Mtok, out ${o}/Mtok'
            except Exception:
                return str(rates)
        parts.append('Preisänderungen:\n' + '\n'.join(
            f'  ~ {k[len("cline::"):]}: {_fmt(o)} → {_fmt(n)}'
            for k, (o, n) in sorted(diff['changed'].items())))
    return '\n\n'.join(parts)


@click.command('check-cline-catalog')
def check_cline_catalog_command():
    """Fetch the Cline catalog, snapshot the committed served list and email the
    owner when model add/remove/change is detected (daily job; mirrors opencode)."""
    try:
        click.echo('Fetching Cline model catalog ...')
        req = urllib.request.Request(
            CLINE_CATALOG_URL,
            headers={'User-Agent': 'ai-provider-service/1.0 (cline catalog sync)'},
        )
        resp = urllib.request.urlopen(req, timeout=30)
        ts = resp.read().decode('utf-8')

        providers = _extract_catalog_providers(ts)
        catalog = _catalog_pricing(providers)  # freshly parsed current catalog
        override = load_existing_cline_pricing()

        new_served = {k: v for k, v in override.items()
                      if k.startswith('cline::') and isinstance(v, dict)}
        old_snap = load_cline_snapshot()

        # First run: seed the snapshot without emailing (no baseline to diff).
        first_run = not old_snap or 'served' not in old_snap
        if first_run:
            path = save_cline_snapshot(new_served)
            click.echo(f'No prior snapshot — seeded {len(new_served)} served IDs '
                       f'to {path} (no notification).')
            return

        diff = diff_cline_served(old_snap, {'served': new_served})
        path = save_cline_snapshot(new_served)

        changed = diff['added'] or diff['removed'] or diff['changed']
        click.echo(f'Cline served list: +{len(diff["added"])}, '
                   f'-{len(diff["removed"])}, ~{len(diff["changed"])} '
                   f'(snapshot -> {path})')
        if changed:
            _send_email(
                'ai-provider: Cline-Modellliste geändert',
                _format_cline_change_email(diff)
                + f'\n\nQuelle: {CLINE_CATALOG_URL}',
                to=CLINE_NOTIFY_EMAIL,
            )
            click.echo('Change detected — notification sent.')
        else:
            click.echo('No model changes since last snapshot.')
    except Exception as e:
        click.echo(f'Error: {e}', err=True)
        raise click.Abort()


@click.command('summary-job')
@click.option('--period', default='day', type=click.Choice(['day', 'app']),
              help='Aggregate by day or by app.')
@click.option('--date', 'date_str', default=None,
              help='Target date (YYYY-MM-DD); for --period=day. Defaults to yesterday.')
@click.option('--app', 'app_name', default=None,
              help='App name; required for --period=app.')
@click.option('--yesterday', is_flag=True, help='Shortcut for --date=<yesterday>.')
def summary_job_command(period, date_str, app_name, yesterday):
    """Run summarization for a calendar day or for an app's last 30 days."""
    from datetime import date, timedelta
    from agents.summary_job import run_for_day, run_for_app

    if period == 'day':
        if yesterday or not date_str:
            target = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        else:
            target = date.fromisoformat(date_str)
        jobs = run_for_day(target)
        click.echo(f'Ran {len(jobs)} summary jobs for {target}.')
        for j in jobs:
            click.echo(f'  {j.user_id}: {j.status} (model={j.model_used or "-"})')
    else:
        if not app_name:
            click.echo('--app=<name> required for --period=app', err=True)
            raise click.Abort()
        jobs = run_for_app(app_name)
        click.echo(f'Ran {len(jobs)} summary jobs for app {app_name}.')
        for j in jobs:
            click.echo(f'  {j.user_id}: {j.status} (model={j.model_used or "-"})')


@click.command('vault-render')
@click.option('--rebuild', is_flag=True, help='Re-render every live note.')
@click.option('--check-stale', 'check_stale', is_flag=True,
              help='Only re-render notes whose DB row is newer than the file (or missing).')
@click.option('--user', default=None, help='Restrict to one user (with --rebuild).')
def vault_render_command(rebuild, check_stale, user):
    """Render or repair the filesystem vault from the database."""
    from storage.vault_renderer import VaultRenderer
    r = VaultRenderer()
    if rebuild:
        n = r.rebuild_all(user_id=user)
        click.echo(f'rendered {n} notes')
    elif check_stale:
        n = r.check_stale()
        removed = r.cleanup_deleted()
        click.echo(f'rendered {n} stale notes; cleaned up {removed} deleted')
    else:
        click.echo('pass --rebuild or --check-stale', err=True)
        raise click.Abort()


@click.command('vault-backup')
@click.option('--output', '-o', default='/tmp',
              help='Directory to write backup files to (default: /tmp).')
@click.option('--db-only', is_flag=True, help='Only back up the SQLite DB, skip vault files.')
def vault_backup_command(output, db_only):
    """Back up the vault directory and SQLite database to a timestamped archive."""
    import tarfile
    from datetime import date
    from pathlib import Path
    from config import Config

    stamp = date.today().isoformat()
    dest = Path(output)
    dest.mkdir(parents=True, exist_ok=True)

    # DB backup
    db_path = Path(Config.DATABASE_URL.replace('sqlite:///', '') or 'storage.db')
    if not db_path.is_absolute():
        db_path = Path(__file__).parent / db_path
    if db_path.exists():
        import shutil
        db_bak = dest / f'{stamp}-storage.db'
        shutil.copy2(str(db_path), str(db_bak))
        click.echo(f'DB backup: {db_bak} ({db_bak.stat().st_size} bytes)')
    else:
        click.echo(f'DB not found at {db_path}', err=True)

    if not db_only:
        vault_root = Path(Config.VAULT_PATH)
        if vault_root.exists() and any(vault_root.iterdir()):
            tar_path = dest / f'{stamp}-vault.tar.gz'
            with tarfile.open(str(tar_path), 'w:gz') as t:
                t.add(str(vault_root), arcname='vault')
            click.echo(f'Vault backup: {tar_path} ({tar_path.stat().st_size} bytes)')
        else:
            click.echo('Vault dir empty or missing, skipped.')


@click.command('refresh-free-models')
def refresh_free_models_command():
    """Proactively refresh hosted free model caches from provider APIs."""
    from providers.opencode import OpencodeClient
    from providers.openrouter import OpenRouterClient

    refreshed = []
    for name, client_cls in (
        ('opencode', OpencodeClient),
        ('openrouter', OpenRouterClient),
    ):
        click.echo(f'Refreshing {name} free models ...')
        free = client_cls.try_refresh_free_models()
        if free:
            click.echo(f'{name}: {len(free)} free models cached: {", ".join(free)}')
            refreshed.append(name)
        else:
            click.echo(f'{name}: no free models found (check config)', err=True)

    if not refreshed:
        raise click.Abort()
