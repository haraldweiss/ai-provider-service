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
    from datetime import date, datetime, timedelta, timezone
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
