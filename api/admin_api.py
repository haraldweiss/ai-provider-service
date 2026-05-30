"""Admin endpoints: grants CRUD + overview JSON.

All routes require ADMIN_TOKEN (enforced via @require_admin).
Mounted at /admin.
"""

from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, g
from database import db
from api.auth import require_admin
from storage.models import ProviderGrant

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.post('/grants')
@require_admin
def create_grant():
    body = request.get_json() or {}
    user_id = body.get('user_id')
    provider_id = body.get('provider_id')
    note = body.get('note')

    if not user_id or not provider_id:
        return jsonify({'error': 'user_id and provider_id required'}), 400

    existing = ProviderGrant.query.filter_by(
        user_id=user_id, provider_id=provider_id).first()

    if existing:
        existing.revoked_at = None
        existing.granted_at = datetime.now(timezone.utc)
        existing.granted_by = g.principal.user_id
        if note is not None:
            existing.note = note
        db.session.commit()
        return jsonify({'grant': existing.to_dict()}), 201

    grant = ProviderGrant(
        user_id=user_id,
        provider_id=provider_id,
        granted_by=g.principal.user_id,
        note=note,
    )
    db.session.add(grant)
    db.session.commit()
    return jsonify({'grant': grant.to_dict()}), 201


@admin_bp.get('/grants')
@require_admin
def list_grants():
    q = ProviderGrant.query
    if request.args.get('user_id'):
        q = q.filter_by(user_id=request.args['user_id'])
    if request.args.get('provider_id'):
        q = q.filter_by(provider_id=request.args['provider_id'])
    if request.args.get('include_revoked', '').lower() != 'true':
        q = q.filter(ProviderGrant.revoked_at.is_(None))
    grants = [g.to_dict() for g in q.order_by(ProviderGrant.granted_at.desc()).all()]
    return jsonify({'grants': grants})


@admin_bp.delete('/grants/<int:grant_id>')
@require_admin
def revoke_grant(grant_id):
    grant = db.session.get(ProviderGrant, grant_id)
    if grant is None:
        return jsonify({'error': 'not found'}), 404
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
    return '', 204
