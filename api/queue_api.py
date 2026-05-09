"""/queue Endpoints: Status + Liste pending Requests."""

import logging
from flask import Blueprint, jsonify, request
from database import db
from api.auth import require_token
from storage.models import RequestQueue

logger = logging.getLogger(__name__)

queue_bp = Blueprint('queue', __name__, url_prefix='/queue')


@queue_bp.get('/<queue_id>')
@require_token
def get_queue_item(queue_id):
    q = RequestQueue.query.get(queue_id)
    if not q:
        return jsonify({'error': 'not_found'}), 404
    return jsonify(q.to_dict())


@queue_bp.get('')
@require_token
def list_queue():
    """Liste pending/done Items, gefiltert per Query-Param `user_id`, `status`."""
    query = RequestQueue.query
    if user_id := request.args.get('user_id'):
        query = query.filter_by(user_id=user_id)
    if status := request.args.get('status'):
        query = query.filter_by(status=status)

    rows = query.order_by(RequestQueue.created_at.desc()).limit(100).all()
    # Bei Listen-View Result weglassen (sonst sehr groß)
    return jsonify({'items': [r.to_dict(include_result=False) for r in rows]})


@queue_bp.delete('/<queue_id>')
@require_token
def cancel_queue_item(queue_id):
    q = RequestQueue.query.get(queue_id)
    if not q:
        return jsonify({'error': 'not_found'}), 404
    if q.status not in ('pending', 'failed'):
        return jsonify({'error': f'kann status={q.status} nicht canceln'}), 400
    db.session.delete(q)
    db.session.commit()
    return jsonify({'message': 'gelöscht'})
