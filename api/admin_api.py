"""Admin endpoints: grants CRUD + overview JSON.

All routes require ADMIN_TOKEN (enforced via @require_admin).
Mounted at /admin.
"""

from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request, g
from database import db
from api.auth import require_admin_or_session
from storage.models import ProviderGrant, ProviderConfig, UsageEvent, UserProfile
from config import Config

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.post('/grants')
@require_admin_or_session
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
@require_admin_or_session
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
@require_admin_or_session
def revoke_grant(grant_id):
    grant = db.session.get(ProviderGrant, grant_id)
    if grant is None:
        return jsonify({'error': 'not found'}), 404
    if grant.revoked_at is None:
        grant.revoked_at = datetime.now(timezone.utc)
        db.session.commit()
    return '', 204


def build_overview() -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    cfg_users = db.session.query(ProviderConfig.user_id).distinct()
    grant_users = db.session.query(ProviderGrant.user_id).distinct()
    usage_users = db.session.query(UsageEvent.user_id).distinct()
    all_user_ids = sorted({
        r[0] for r in cfg_users.union(grant_users).union(usage_users).all()
    })

    profile_map = {p.user_id: p for p in UserProfile.query.all()}

    out = []
    for uid in all_user_ids:
        profile = profile_map.get(uid)
        if profile and profile.disabled:
            continue

        configured = [r.provider_id for r in
                      ProviderConfig.query.filter_by(user_id=uid).all()]

        grants = [g.to_dict() for g in
                  ProviderGrant.query.filter_by(user_id=uid)
                  .filter(ProviderGrant.revoked_at.is_(None)).all()]

        events = UsageEvent.query.filter(
            UsageEvent.user_id == uid,
            UsageEvent.created_at >= cutoff,
        ).all()

        by_provider = {}
        by_origin = {}
        last_used = None
        for ev in events:
            by_provider[ev.provider_id] = by_provider.get(ev.provider_id, 0) + 1
            if ev.origin_app:
                by_origin[ev.origin_app] = by_origin.get(ev.origin_app, 0) + 1
            if last_used is None or (ev.created_at and ev.created_at > last_used):
                last_used = ev.created_at

        out.append({
            'user_id': uid,
            'alias': profile.alias if profile else None,
            'is_admin': uid == Config.ADMIN_USER_ID,
            'configured_providers': sorted(configured),
            'grants': grants,
            'last_30d': {
                'total_calls': len(events),
                'by_provider': by_provider,
                'by_origin_app': by_origin,
                'last_used_at': last_used.isoformat() if last_used else None,
            },
        })
    return out


@admin_bp.post('/users')
@require_admin_or_session
def create_user_profile():
    body = request.get_json() or {}
    user_id = body.get('user_id')
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400
    existing = db.session.get(UserProfile, user_id)
    if existing:
        existing.alias = body.get('alias', existing.alias)
        existing.disabled = False
        db.session.commit()
        return jsonify({'user': existing.to_dict()}), 200
    profile = UserProfile(user_id=user_id, alias=body.get('alias'))
    db.session.add(profile)
    db.session.commit()
    return jsonify({'user': profile.to_dict()}), 201


@admin_bp.patch('/users/<user_id>')
@require_admin_or_session
def update_user_profile(user_id):
    profile = db.session.get(UserProfile, user_id)
    if not profile:
        return jsonify({'error': 'user not found'}), 404
    body = request.get_json() or {}
    if 'alias' in body:
        profile.alias = body['alias']
    if 'disabled' in body:
        profile.disabled = bool(body['disabled'])
    db.session.commit()
    return jsonify({'user': profile.to_dict()})


@admin_bp.delete('/users/<user_id>')
@require_admin_or_session
def delete_user_profile(user_id):
    profile = db.session.get(UserProfile, user_id)
    if not profile:
        profile = UserProfile(user_id=user_id, disabled=True)
        db.session.add(profile)
    else:
        profile.disabled = True
    db.session.commit()
    return '', 204


@admin_bp.get('/overview')
@require_admin_or_session
def overview():
    return jsonify({'users': build_overview()})
