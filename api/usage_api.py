# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /usage/events — read-only endpoint for the Claude Usage Tracker.

Pagination uses an opaque timestamp/ID cursor. Legacy `since` remains supported.
"""
from __future__ import annotations
from datetime import datetime, timezone
from flask import Blueprint, g, jsonify, request
from sqlalchemy import and_, or_

from api.auth import require_token
from storage.models import UsageEvent

bp = Blueprint('usage_api', __name__)


@bp.route('/usage/events', methods=['GET'])
@require_token
def list_events():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400

    cursor_raw = request.args.get('cursor')
    cursor_id = None
    since_raw = request.args.get('since')
    since_dt = None
    if cursor_raw:
        try:
            since_raw, id_raw = cursor_raw.rsplit('|', 1)
            cursor_id = int(id_raw)
            if not since_raw or not 1 <= cursor_id <= 9223372036854775807:
                raise ValueError
        except ValueError:
            return jsonify({'error': 'invalid cursor'}), 400
    if since_raw:
        try:
            since_dt = datetime.fromisoformat(since_raw)
            if since_dt.tzinfo:
                since_dt = since_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return jsonify({'error': 'invalid since timestamp'}), 400

    try:
        limit = int(request.args.get('limit', 500))
    except ValueError:
        return jsonify({'error': 'invalid limit'}), 400
    limit = max(1, min(limit, 2000))

    q = UsageEvent.query.filter_by(user_id=user_id)
    if since_dt is not None:
        condition = UsageEvent.created_at > since_dt
        if cursor_id is not None:
            condition = or_(condition, and_(UsageEvent.created_at == since_dt,
                                           UsageEvent.id > cursor_id))
        q = q.filter(condition)
    rows = q.order_by(UsageEvent.created_at.asc(), UsageEvent.id.asc()).limit(limit + 1).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    return jsonify({
        'events': [r.to_dict() for r in rows],
        'count': len(rows),
        'next_since': rows[-1].created_at.isoformat() if rows else since_raw,
        'next_cursor': f'{rows[-1].created_at.isoformat()}|{rows[-1].id}' if rows else cursor_raw,
        'has_more': has_more,
    })
@bp.route('/usage/users', methods=['GET'])
@require_token
def list_known_users():
    """Return all known user IDs with their aliases (for tracker discovery)."""
    from storage.models import UserAccessToken, UserProfile
    tokens_query = UserAccessToken.query
    profiles_query = UserProfile.query
    if g.principal.credential == 'user_token':
        tokens_query = tokens_query.filter_by(user_id=g.principal.user_id)
        profiles_query = profiles_query.filter_by(user_id=g.principal.user_id)
    tokens = tokens_query.all()
    profile_rows = profiles_query.all()
    profiles = {p.user_id: p.alias for p in profile_rows}
    seen = set()
    users = []
    for t in tokens:
        seen.add(t.user_id)
        users.append({'user_id': t.user_id, 'alias': profiles.get(t.user_id)})
    for p in profile_rows:
        if p.user_id not in seen:
            users.append({'user_id': p.user_id, 'alias': p.alias})
    return jsonify({'users': users})
