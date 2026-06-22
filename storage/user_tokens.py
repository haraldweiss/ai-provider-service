"""Issue and verify high-entropy, per-user bearer tokens."""

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from database import db
from storage.models import UserAccessToken


def _digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def issue_user_token(user_id: str) -> str:
    if not user_id or len(user_id) > 255:
        raise ValueError('valid user_id required')
    raw = f'aips_{secrets.token_urlsafe(32)}'
    row = db.session.get(UserAccessToken, user_id)
    if row is None:
        row = UserAccessToken(user_id=user_id)
    row.token_hash = _digest(raw)
    row.token_prefix = raw[:12]
    row.generation = secrets.token_hex(16)
    row.created_at = datetime.now(timezone.utc)
    row.revoked_at = None
    db.session.add(row)
    db.session.commit()
    return raw


def resolve_user_token(raw_token: str):
    if not raw_token or not raw_token.startswith('aips_'):
        return None
    digest = _digest(raw_token)
    row = UserAccessToken.query.filter_by(token_hash=digest, revoked_at=None).first()
    if row is None or not hmac.compare_digest(row.token_hash, digest):
        return None
    return row.user_id, row.generation


def is_user_token_generation_active(user_id: str, generation: str) -> bool:
    row = db.session.get(UserAccessToken, user_id)
    return bool(
        row and row.revoked_at is None and generation
        and hmac.compare_digest(row.generation, generation)
    )


def revoke_user_token(user_id: str) -> bool:
    row = db.session.get(UserAccessToken, user_id)
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    db.session.commit()
    return True
