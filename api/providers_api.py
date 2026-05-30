"""/providers Endpoints: Liste, Models, Health."""

import logging
from flask import Blueprint, jsonify, request
from api.auth import require_token
from api.gate import require_provider_access, is_allowed
from providers import get_client, PROVIDER_REGISTRY, list_provider_ids
from flask import g
from storage.models import ProviderConfig
import health_tracker

logger = logging.getLogger(__name__)

providers_bp = Blueprint('providers', __name__, url_prefix='/providers')


@providers_bp.get('')
@require_token
def list_providers():
    """Liste aller bekannten Provider mit Status pro user_id (optional via query)."""
    from dispatcher import _is_claude_server_key_allowed

    user_id = request.args.get('user_id')

    out = []
    for pid, meta in PROVIDER_REGISTRY.items():
        configured = False
        if user_id:
            pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=pid).first()
            configured = pc is not None

        # System-Provider gelten als "configured" — Ollama gar keine Credentials
        # nötig, Claude wenn ANTHROPIC_API_KEY gesetzt ist + User darf den
        # Server-Key benutzen (Allowlist).
        if meta['system']:
            if pid == 'claude' and user_id and not _is_claude_server_key_allowed(user_id):
                # User darf den Server-Key nicht — zeigt sich im Frontend als
                # "nicht konfiguriert" und löst das Config-Form aus.
                pass
            else:
                configured = True

        allowed = is_allowed(g.principal, pid)
        health = health_tracker.get_status(pid)
        out.append({
            'id': pid,
            'name': meta['name'],
            'system': meta['system'],
            'requires': meta['requires'],
            'optional': meta['optional'],
            'configured': configured,
            'allowed': allowed,
            'healthy': health.get('healthy'),
            'last_check': health.get('updated_at'),
        })
    return jsonify({'providers': out})


@providers_bp.get('/<provider_id>/models')
@require_token
@require_provider_access('provider_id')
def get_models(provider_id):
    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Provider: {provider_id}'}), 404

    user_id = request.args.get('user_id')
    cfg = {}
    if user_id:
        pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
        if pc:
            cfg = pc.get_config()
        elif not PROVIDER_REGISTRY[provider_id]['system']:
            return jsonify({'error': 'Provider nicht konfiguriert', 'configured': False}), 400

    try:
        client = get_client(provider_id, cfg)
        models = client.get_models()
        return jsonify({'models': models, 'count': len(models)})
    except Exception as e:
        logger.warning(f'get_models({provider_id}) failed: {e}')
        return jsonify({'error': str(e)}), 502


@providers_bp.get('/<provider_id>/health')
@require_token
@require_provider_access('provider_id')
def get_health(provider_id):
    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Provider: {provider_id}'}), 404
    return jsonify({'provider_id': provider_id, **health_tracker.get_status(provider_id)})


@providers_bp.post('/<provider_id>/test')
@require_token
@require_provider_access('provider_id')
def test_provider(provider_id):
    """Live-Test: holt Models. user_id aus Body."""
    if provider_id not in PROVIDER_REGISTRY:
        return jsonify({'error': f'Unbekannter Provider: {provider_id}'}), 404

    body = request.get_json() or {}
    user_id = body.get('user_id')

    cfg = {}
    if user_id:
        pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
        if pc:
            cfg = pc.get_config()

    try:
        client = get_client(provider_id, cfg)
        models = client.get_models()
        health_tracker.set_status(provider_id, True)
        return jsonify({
            'status': 'connected',
            'provider_id': provider_id,
            'models_available': len(models),
            'sample_models': models[:5],
        })
    except Exception as e:
        health_tracker.set_status(provider_id, False, reason=str(e))
        return jsonify({'status': 'error', 'error': str(e)}), 400
