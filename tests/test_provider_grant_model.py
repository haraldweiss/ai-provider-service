# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ProviderGrant model: insert, uniqueness, soft-delete semantics."""

from datetime import datetime, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from database import db
from storage.models import ProviderGrant


def test_create_grant(app):
    with app.app_context():
        g = ProviderGrant(
            user_id='lisa',
            provider_id='claude',
            granted_by='harald',
            note='for transcript summarization',
        )
        db.session.add(g)
        db.session.commit()

        assert g.id is not None
        assert g.granted_at is not None
        assert g.revoked_at is None


def test_unique_user_provider(app):
    with app.app_context():
        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        db.session.commit()

        db.session.add(ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald'))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_soft_delete(app):
    with app.app_context():
        g = ProviderGrant(
            user_id='lisa', provider_id='claude', granted_by='harald')
        db.session.add(g)
        db.session.commit()

        g.revoked_at = datetime.now(timezone.utc)
        db.session.commit()

        fresh = ProviderGrant.query.filter_by(id=g.id).first()
        assert fresh.revoked_at is not None
