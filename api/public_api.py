"""Public/user-facing API endpoints for self-service registration and grant requests."""

from datetime import datetime, timezone
import logging
from flask import Blueprint, jsonify, request, g
from database import db
from storage.models import AdminNotification, GrantRequest, ProviderGrant, UserProfile
from config import Config
from api.auth import require_token
from storage.user_tokens import issue_user_token
from api.notifications import get_notification_service

public_bp = Blueprint('public', __name__, url_prefix='')


@public_bp.get('/health')
def health():
    return jsonify({'status': 'ok'}), 200


# ---- User Registration / Profile ----

@public_bp.get('/register')
def get_register_info():
    """Check if user exists or show registration info."""
    user_id = request.args.get('user_id') or request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'error': 'user_id query param or X-User-ID header required'}), 400

    profile = db.session.get(UserProfile, user_id)
    grant = ProviderGrant.query.filter_by(user_id=user_id, revoked_at=None).all()

    return jsonify({
        'user_id': user_id,
        'exists': profile is not None,
        'alias': profile.alias if profile else None,
        'grants': [g.to_dict() for g in grant],
        'message': 'Use POST /register to create your profile' if not profile else 'Profile exists',
    })


@public_bp.post('/register')
def register_user():
    """Create or update user profile (self-registration).
    
    Body: { user_id, alias?, email? }
    """
    body = request.get_json() or {}
    user_id = body.get('user_id')
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400

    profile = db.session.get(UserProfile, user_id)
    if profile:
        # Update alias if provided
        if 'alias' in body:
            profile.alias = body['alias']
        if 'email' in body:
            profile.email = body.get('email')
        profile.disabled = False
        db.session.commit()
        return jsonify({'user': profile.to_dict(), 'message': 'Profile updated'}), 200

    profile = UserProfile(
        user_id=user_id,
        alias=body.get('alias'),
        email=body.get('email'),
    )
    db.session.add(profile)
    db.session.commit()

    # Send admin notification
    try:
        ns = get_notification_service()
        if ns:
            ns.notify_user_registration(user_id)
    except Exception as e:
        logger.warning(f"Failed to send registration notification: {e}")

    return jsonify({'user': profile.to_dict(), 'message': 'Profile created'}), 201


@public_bp.get('/me')
@require_token
def get_me():
    """Get current user's profile and grants (requires valid user token)."""
    user_id = g.user_id
    profile = db.session.get(UserProfile, user_id)
    grants = ProviderGrant.query.filter_by(user_id=user_id, revoked_at=None).all()
    grant_requests = GrantRequest.query.filter_by(user_id=user_id).order_by(GrantRequest.requested_at.desc()).all()

    return jsonify({
        'user': profile.to_dict() if profile else {'user_id': user_id},
        'grants': [g.to_dict() for g in grants],
        'grant_requests': [r.to_dict() for r in grant_requests],
    })


# ---- Grant Requests (Provider Access Requests) ----

@public_bp.post('/grant-requests')
@require_token
def create_grant_request():
    """Request access to a provider.
    
    Body: { provider_id, reason? }
    """
    user_id = g.user_id
    body = request.get_json() or {}
    provider_id = body.get('provider_id')
    reason = body.get('reason')

    if not provider_id:
        return jsonify({'error': 'provider_id required'}), 400

    # Check if already has an active grant
    existing_grant = ProviderGrant.query.filter_by(
        user_id=user_id, provider_id=provider_id, revoked_at=None
    ).first()
    if existing_grant:
        return jsonify({'error': 'Already granted access to this provider', 'grant': existing_grant.to_dict()}), 400

    # Check if pending request exists
    existing_request = GrantRequest.query.filter_by(
        user_id=user_id, provider_id=provider_id, status='pending'
    ).first()
    if existing_request:
        return jsonify({'error': 'Request already pending', 'request': existing_request.to_dict()}), 400

    # Check if previously denied - allow re-request
    denied_request = GrantRequest.query.filter_by(
        user_id=user_id, provider_id=provider_id, status='denied'
    ).first()
    if denied_request:
        # Update to pending again
        denied_request.status = 'pending'
        denied_request.reason = reason or denied_request.reason
        denied_request.requested_at = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({'request': denied_request.to_dict(), 'message': 'Request re-submitted'}), 200

    request_obj = GrantRequest(
        user_id=user_id,
        provider_id=provider_id,
        reason=reason,
    )
    db.session.add(request_obj)
    db.session.commit()

    # Send admin notification
    try:
        ns = get_notification_service()
        if ns:
            ns.notify_grant_request(user_id, provider_id, reason)
    except Exception as e:
        logger.warning(f"Failed to send grant request notification: {e}")

    return jsonify({'request': request_obj.to_dict(), 'message': 'Grant request submitted'}), 201


@public_bp.get('/grant-requests')
@require_token
def list_grant_requests():
    """List current user's grant requests."""
    user_id = g.user_id
    status_filter = request.args.get('status')
    q = GrantRequest.query.filter_by(user_id=user_id)
    if status_filter:
        q = q.filter_by(status=status_filter)
    requests = q.order_by(GrantRequest.requested_at.desc()).all()
    return jsonify({'requests': [r.to_dict() for r in requests]})


@public_bp.get('/grant-requests/<int:request_id>')
@require_token
def get_grant_request(request_id):
    """Get a specific grant request."""
    user_id = g.user_id
    req = db.session.get(GrantRequest, request_id)
    if not req or req.user_id != user_id:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'request': req.to_dict()})


# ---- Token Management ----

@public_bp.post('/token')
@require_token
def get_or_create_token():
    """Issue or return existing user access token."""
    from storage.user_tokens import get_user_token
    user_id = g.user_id
    token = get_user_token(user_id)
    if not token:
        from storage.user_tokens import issue_user_token
        token = issue_user_token(user_id)
    return jsonify({'token': token}), 201


@public_bp.get('/token')
@require_token
def get_token():
    """Get current token info (without the raw token)."""
    from storage.user_tokens import get_user_token
    user_id = g.user_id
    token_info = get_user_token(user_id)
    if not token_info:
        return jsonify({'configured': False}), 404
    return jsonify({'configured': True, 'token_status': token_info})


