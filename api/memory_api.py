"""Memory API — notes/events/audit/summarize/list endpoints.

Auth: all endpoints require a Bearer token. User scoping uses _asserted_user_id
from api.auth. Admin token may pass ?user= to read other users' notes; never
to write on their behalf.
"""

from __future__ import annotations
from flask import Blueprint, request, jsonify, g
from sqlalchemy import or_
from database import db
from config import Config
from api.auth import require_token, _asserted_user_id
from storage.memory_models import MemoryNote, MemoryKind
from storage.memory import MemoryWriter, NoteAlreadyExists
from storage.vault_renderer import VaultRenderer

memory_bp = Blueprint('memory', __name__, url_prefix='/memory')

_BODY_MAX = 1024 * 1024  # 1 MiB


def _gate():
    if not Config.MEMORY_ENABLED:
        return jsonify({'error': 'memory feature disabled'}), 503
    return None


def _scope_user_id() -> str:
    """For non-admin tokens, force user_id to principal's user_id.
    Admin tokens may override via ?user=."""
    if g.principal.role == 'admin':
        return request.args.get('user') or _asserted_user_id() or g.principal.user_id
    return g.principal.user_id


@memory_bp.post('/notes')
@require_token
def create_note():
    gate = _gate()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    user_id = _scope_user_id()
    if g.principal.role != 'admin' and body.get('user_id') and body['user_id'] != user_id:
        return jsonify({'error': 'cross-user write forbidden'}), 403

    text_body = body.get('body', '') or ''
    if len(text_body.encode('utf-8')) > _BODY_MAX:
        return jsonify({'error': 'body too large'}), 413

    try:
        note = MemoryWriter().write_note(
            user_id=user_id,
            app=body.get('app') or 'gateway',
            title=body.get('title') or '',
            body=text_body,
            tags=body.get('tags') or [],
            folder=body.get('folder'),
            slug=body.get('slug'),
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    try:
        VaultRenderer().render_one(note)
        render_pending = False
    except Exception:
        render_pending = True

    return jsonify({'id': note.id,
                    'path': f'{note.folder}/{note.slug}.md',
                    'render_pending': render_pending}), 201


@memory_bp.get('/notes')
@require_token
def list_notes():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.deleted_at.is_(None),
    )

    kind = request.args.get('kind')
    if kind:
        try:
            q = q.filter(MemoryNote.kind == MemoryKind(kind))
        except ValueError:
            return jsonify({'error': f'unknown kind: {kind}'}), 400

    if app_filter := request.args.get('app'):
        q = q.filter(MemoryNote.app == app_filter)
    if folder := request.args.get('folder'):
        q = q.filter(MemoryNote.folder == folder)
    if text := request.args.get('q'):
        pat = f'%{text}%'
        q = q.filter(or_(MemoryNote.title.like(pat), MemoryNote.body.like(pat)))

    try:
        limit = min(int(request.args.get('limit', '50')), 500)
        offset = max(int(request.args.get('offset', '0')), 0)
    except ValueError:
        return jsonify({'error': 'limit/offset must be integers'}), 400

    total = q.count()
    rows = (q.order_by(MemoryNote.created_at.desc())
              .limit(limit).offset(offset).all())
    return jsonify({'notes': [r.to_dict() for r in rows], 'total': total})


@memory_bp.get('/notes/<int:note_id>')
@require_token
def get_note(note_id: int):
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    n = MemoryNote.query.filter_by(id=note_id, user_id=user_id).first()
    if not n or n.deleted_at is not None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(n.to_dict())


@memory_bp.patch('/notes/<int:note_id>')
@require_token
def patch_note(note_id: int):
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    n = MemoryNote.query.filter_by(id=note_id, user_id=user_id).first()
    if not n or n.deleted_at is not None:
        return jsonify({'error': 'not found'}), 404
    if n.kind != MemoryKind.NOTE:
        return jsonify({'error': f'cannot edit kind={n.kind.value}'}), 403

    body = request.get_json(silent=True) or {}
    new_body = body.get('body')
    if new_body is not None:
        if len(new_body.encode('utf-8')) > _BODY_MAX:
            return jsonify({'error': 'body too large'}), 413
        n.body = new_body
    if 'title' in body:
        n.title = body['title'] or ''
    if 'tags' in body:
        n.tags = body['tags'] or []
    db.session.commit()
    try:
        VaultRenderer().render_one(n)
    except Exception:
        pass
    return jsonify(n.to_dict())


@memory_bp.delete('/notes/<int:note_id>')
@require_token
def delete_note(note_id: int):
    gate = _gate()
    if gate:
        return gate
    from datetime import datetime, timezone
    user_id = _scope_user_id()
    n = MemoryNote.query.filter_by(id=note_id, user_id=user_id).first()
    if not n or n.deleted_at is not None:
        return jsonify({'error': 'not found'}), 404
    n.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    try:
        VaultRenderer().cleanup_deleted()
    except Exception:
        pass
    return ('', 204)


@memory_bp.post('/events')
@require_token
def create_event():
    gate = _gate()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    user_id = _scope_user_id()
    if g.principal.role != 'admin' and body.get('user_id') and body['user_id'] != user_id:
        return jsonify({'error': 'cross-user write forbidden'}), 403
    if not body.get('event_type'):
        return jsonify({'error': 'event_type required'}), 400
    try:
        note = MemoryWriter().write_event(
            user_id=user_id,
            app=body.get('app') or 'gateway',
            event_type=body['event_type'],
            payload=body.get('payload') or {},
            tags=body.get('tags') or [],
            slug=body.get('slug'),
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    try:
        VaultRenderer().render_one(note)
        render_pending = False
    except Exception:
        render_pending = True
    return jsonify({'id': note.id,
                    'path': f'{note.folder}/{note.slug}.md',
                    'render_pending': render_pending}), 201


@memory_bp.get('/events')
@require_token
def list_events():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.kind == MemoryKind.EVENT,
        MemoryNote.deleted_at.is_(None),
    )
    if et := request.args.get('event_type'):
        q = q.filter(MemoryNote.folder.like(f'%/events/{et}'))
    if app_filter := request.args.get('app'):
        q = q.filter(MemoryNote.app == app_filter)
    try:
        limit = min(int(request.args.get('limit', '50')), 500)
    except ValueError:
        return jsonify({'error': 'limit must be integer'}), 400
    rows = q.order_by(MemoryNote.created_at.desc()).limit(limit).all()
    return jsonify({'events': [r.to_dict() for r in rows]})
