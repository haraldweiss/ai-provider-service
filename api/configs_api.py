"""/configs Endpoints: per-User Provider-Konfigurationen verwalten."""

import logging
from flask import Blueprint, jsonify, request
from database import db
from api.auth import require_token
from api.gate import has_personal_api_key, is_allowed
from providers import PROVIDER_REGISTRY, provider_supports_personal_key
from flask import g
from storage.models import ProviderConfig

logger = logging.getLogger(__name__)

configs_bp = Blueprint('configs', __name__, url_prefix='/configs')


def _can_manage(user_id, provider_id, config_dict=None, read_or_delete=False):
    principal = g.principal
    if principal.role == 'admin' or is_allowed(principal, provider_id):
        return True
    if principal.user_id != user_id or not provider_supports_personal_key(provider_id):
        return False
    if read_or_delete:
        return True
    return bool(
        has_personal_api_key(user_id, provider_id)
        or str((config_dict or {}).get('api_key') or '').strip()
    )


def _access_denied(provider_id, user_id):
    return jsonify({
        'error': 'needs_approval',
        'provider_id': provider_id,
        'user_id': user_id,
        'message': 'Admin approval or a personal API key is required',
    }), 403


@configs_bp.get('/<user_id>')
@require_token
def list_configs(user_id):
    rows = ProviderConfig.query.filter_by(user_id=user_id).all()
    return jsonify({'user_id': user_id, 'configs': [r.to_safe_dict() for r in rows]})


@configs_bp.get('/<user_id>/<provider_id>')
@require_token
def get_config(user_id, provider_id):
    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Provider: {provider_id}'}), 400
    if not _can_manage(user_id, provider_id, read_or_delete=True):
        return _access_denied(provider_id, user_id)
    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
    if not pc:
        return jsonify({'configured': False, 'provider_id': provider_id}), 200
    return jsonify(pc.to_safe_dict())


@configs_bp.post('/<user_id>/<provider_id>')
@require_token
def save_config(user_id, provider_id):
    """Speichert oder aktualisiert eine Provider-Config.

    Body:
      {
        "config": { "api_key": "...", "api_endpoint": "...", "organization_id": "..." },
        "fallback_provider": "claude" | null,
        "queue_when_unavailable": true,
        "queue_ttl_hours": 24
      }
    """
    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Provider: {provider_id}'}), 400

    body = request.get_json() or {}
    config_dict = body.get('config') or {}

    if not _can_manage(user_id, provider_id, config_dict=config_dict):
        return _access_denied(provider_id, user_id)

    # Pflichtfelder validieren
    required = PROVIDER_REGISTRY[provider_id]['requires']
    missing = [f for f in required if not config_dict.get(f)]
    if missing:
        return jsonify({'error': f'Pflichtfelder fehlen: {", ".join(missing)}'}), 400

    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()

    # Wenn Update + api_key leer → bestehenden Wert beibehalten
    if pc and not config_dict.get('api_key'):
        try:
            old = pc.get_config()
            if old.get('api_key'):
                config_dict['api_key'] = old['api_key']
        except Exception:
            pass

    if not pc:
        pc = ProviderConfig(user_id=user_id, provider_id=provider_id)
        db.session.add(pc)

    pc.set_config(config_dict)
    if 'fallback_provider' in body:
        pc.fallback_provider = body['fallback_provider'] or None
    if 'queue_when_unavailable' in body:
        pc.queue_when_unavailable = bool(body['queue_when_unavailable'])
    if 'queue_ttl_hours' in body:
        pc.queue_ttl_hours = int(body['queue_ttl_hours'])

    db.session.commit()
    return jsonify({'message': 'gespeichert', **pc.to_safe_dict()})


@configs_bp.delete('/<user_id>/<provider_id>')
@require_token
def delete_config(user_id, provider_id):
    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Provider: {provider_id}'}), 400
    if not _can_manage(user_id, provider_id, read_or_delete=True):
        return _access_denied(provider_id, user_id)
    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
    if not pc:
        return jsonify({'message': 'kein Config-Eintrag vorhanden'}), 200
    db.session.delete(pc)
    db.session.commit()
    return jsonify({'message': 'gelöscht'})
