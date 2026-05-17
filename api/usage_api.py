# SPDX-License-Identifier: AGPL-3.0-or-later
"""GET /usage/events — read-only endpoint for the Claude Usage Tracker.

Pagination via since-cursor + limit. Returns oldest-first within the page so
the caller can advance `since` to the last event's timestamp.
"""
from __future__ import annotations
from datetime import datetime
from flask import Blueprint, jsonify, request

from api.auth import require_token
from storage.models import UsageEvent

bp = Blueprint('usage_api', __name__)


@bp.route('/usage/events', methods=['GET'])
@require_token
def list_events():
    user_id = request.args.get('user_id')
    if not user_id:
        return jsonify({'error': 'user_id required'}), 400

    since_raw = request.args.get('since')
    since_dt = None
    if since_raw:
        try:
            since_dt = datetime.fromisoformat(since_raw)
        except ValueError:
            return jsonify({'error': 'invalid since timestamp'}), 400

    try:
        limit = int(request.args.get('limit', 500))
    except ValueError:
        return jsonify({'error': 'invalid limit'}), 400
    limit = max(1, min(limit, 2000))

    q = UsageEvent.query.filter_by(user_id=user_id)
    if since_dt is not None:
        q = q.filter(UsageEvent.created_at > since_dt)
    rows = q.order_by(UsageEvent.created_at.asc()).limit(limit).all()

    return jsonify({
        'events': [r.to_dict() for r in rows],
        'count': len(rows),
        'next_since': rows[-1].created_at.isoformat() if rows else since_raw,
        'has_more': len(rows) == limit,
    })
