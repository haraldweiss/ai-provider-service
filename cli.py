"""Flask CLI commands for ai-provider-service.

Register with: app.cli.add_command(...) in app.create_app().

Commands:
  grants-bootstrap: insert one active grant per existing (user_id, provider_id)
    in provider_configs where provider_id is NOT in Config.UNGATED_PROVIDERS.
    Idempotent.
"""

from __future__ import annotations
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
