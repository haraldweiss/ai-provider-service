"""Provider access gate.

is_allowed(principal, provider_id) returns True iff:
  - Config.GATE_ENABLED is False (kill switch), OR
  - provider_id is in Config.UNGATED_PROVIDERS, OR
  - principal.role == 'admin', OR
  - an active (un-revoked) ProviderGrant exists for (user_id, provider_id).

@require_provider_access(arg_name) decorator pulls provider_id from
view_args, query, or JSON body and gates the route. Must be used AFTER
@require_token so g.principal is set.
"""

from __future__ import annotations

from functools import wraps
import logging
from flask import jsonify, g, request
from config import Config
from storage.models import ProviderGrant, ProviderConfig
from api.auth import Principal
from providers import provider_supports_personal_key

logger = logging.getLogger(__name__)


def has_personal_api_key(user_id: str, provider_id: str) -> bool:
    if not user_id or not provider_supports_personal_key(provider_id):
        return False
    row = ProviderConfig.query.filter_by(
        user_id=user_id, provider_id=provider_id,
    ).first()
    if row is None:
        return False
    try:
        return bool(str(row.get_config().get('api_key') or '').strip())
    except Exception:
        logger.warning(
            'Personal provider config could not be decrypted: user=%s provider=%s',
            user_id, provider_id,
        )
        return False


def is_allowed(principal: Principal, provider_id: str) -> bool:
    if not Config.GATE_ENABLED:
        return True
    if provider_id in Config.UNGATED_PROVIDERS:
        return True
    if principal.role == 'admin':
        return True
    if has_personal_api_key(principal.user_id, provider_id):
        return True
    grant = ProviderGrant.query.filter_by(
        user_id=principal.user_id,
        provider_id=provider_id,
    ).filter(ProviderGrant.revoked_at.is_(None)).first()
    return grant is not None


def _extract_provider_id(arg_name: str) -> str | None:
    if request.view_args and arg_name in request.view_args:
        return request.view_args[arg_name]
    if request.args.get(arg_name):
        return request.args[arg_name]
    if request.is_json:
        body = request.get_json(silent=True) or {}
        # /chat uses 'provider' not 'provider_id' — accept both.
        return body.get(arg_name) or body.get('provider')
    return None


def require_provider_access(arg_name: str = 'provider_id'):
    """Decorator: 403 if g.principal lacks access to provider in path/query/body."""
    def deco(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if not getattr(g, 'principal', None):
                return jsonify({'error': 'unauthenticated'}), 401
            provider_id = _extract_provider_id(arg_name)
            if not provider_id:
                return jsonify({'error': f'missing {arg_name}'}), 400
            if not is_allowed(g.principal, provider_id):
                return jsonify({
                    'error': 'needs_approval',
                    'provider_id': provider_id,
                    'user_id': g.principal.user_id,
                    'message': f'Provider {provider_id} requires admin approval for this user',
                }), 403
            return f(*args, **kwargs)
        return wrapped
    return deco
