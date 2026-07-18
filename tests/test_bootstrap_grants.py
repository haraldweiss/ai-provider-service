# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the grants-bootstrap CLI command."""

from config import Config
from database import db
from storage.models import ProviderConfig, ProviderGrant


def test_bootstrap_creates_grants_for_gated_configs(app):
    Config.UNGATED_PROVIDERS = {'ollama'}
    with app.app_context():
        for uid, pid in [('lisa', 'ollama'), ('lisa', 'claude'),
                         ('bob', 'openai')]:
            pc = ProviderConfig(user_id=uid, provider_id=pid)
            pc.set_config({})
            db.session.add(pc)
        db.session.commit()

        from cli import bootstrap_grants
        created = bootstrap_grants()
        assert created == 2  # claude + openai; ollama is ungated

        grants = ProviderGrant.query.all()
        assert {(g.user_id, g.provider_id) for g in grants} == {
            ('lisa', 'claude'), ('bob', 'openai')
        }


def test_bootstrap_idempotent(app):
    Config.UNGATED_PROVIDERS = {'ollama'}
    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='claude')
        pc.set_config({})
        db.session.add(pc)
        db.session.commit()

        from cli import bootstrap_grants
        first = bootstrap_grants()
        second = bootstrap_grants()
        assert first == 1
        assert second == 0


def test_bootstrap_skips_ungated(app):
    Config.UNGATED_PROVIDERS = {'ollama'}
    with app.app_context():
        pc = ProviderConfig(user_id='lisa', provider_id='ollama')
        pc.set_config({})
        db.session.add(pc)
        db.session.commit()

        from cli import bootstrap_grants
        assert bootstrap_grants() == 0
        assert ProviderGrant.query.count() == 0
