"""Vault export API — tarball of user's vault subtree + single-file download.

Path-traversal is enforced via Path.resolve().relative_to(root): the resolved
file path must be a descendant of VAULT_PATH/<user>. Refuses absolute paths
and `..` components before any filesystem call.
"""

from __future__ import annotations
import io
import os
import tarfile
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, g
from config import Config
from api.auth import require_token, _asserted_user_id

vault_bp = Blueprint('vault', __name__, url_prefix='/memory')


def _gate():
    if not Config.MEMORY_ENABLED:
        return jsonify({'error': 'memory feature disabled'}), 503
    return None


def _scope_user_id() -> str:
    if g.principal.role == 'admin':
        return request.args.get('user') or _asserted_user_id() or g.principal.user_id
    return g.principal.user_id


@vault_bp.get('/vault.tar.gz')
@require_token
def vault_tarball():
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    root = Path(Config.VAULT_PATH) / user_id
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        if root.exists():
            t.add(str(root), arcname=user_id)
    buf.seek(0)
    return send_file(buf, mimetype='application/gzip',
                     as_attachment=True,
                     download_name=f'{user_id}-vault.tar.gz')


@vault_bp.get('/vault/<path:relpath>')
@require_token
def vault_file(relpath: str):
    gate = _gate()
    if gate:
        return gate
    user_id = _scope_user_id()
    if not relpath or relpath.startswith('/') or '..' in relpath.split('/'):
        return jsonify({'error': 'invalid path'}), 400
    root = (Path(Config.VAULT_PATH) / user_id).resolve()
    candidate = (root / relpath).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return jsonify({'error': 'invalid path'}), 400
    if not candidate.exists() or not candidate.is_file():
        return jsonify({'error': 'not found'}), 404
    return send_file(str(candidate), mimetype='text/markdown')
