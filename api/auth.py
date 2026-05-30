"""Bearer-Token-Auth with Principal resolution.

Two tokens are recognized:
- ADMIN_TOKEN: resolves to Principal(user_id=ADMIN_USER_ID, role='admin')
               and bypasses the provider gate. Body/query user_id is ignored
               for admin tokens so the principal's user_id is unambiguous.
- SERVICE_TOKEN: resolves to Principal(user_id=<asserted>, role='user').
                 user_id taken from body JSON, query string, or path arg.

A new X-Agent header is read into g.agent (string, may be None). Used
informationally — currently only flowed into UsageEvent.origin_app when the
caller hasn't set X-Origin-App. No policy impact.
"""

import hmac
from dataclasses import dataclass
from functools import wraps
from flask import request, jsonify, g
from config import Config


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str  # 'admin' | 'user'


def _asserted_user_id() -> str:
    """Pull user_id from JSON body, then query string, then path args."""
    if request.is_json:
        body = request.get_json(silent=True) or {}
        if body.get('user_id'):
            return str(body['user_id'])
    if request.args.get('user_id'):
        return request.args['user_id']
    if request.view_args and 'user_id' in request.view_args:
        return request.view_args['user_id']
    return ''


def _resolve_principal():
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return None
    token = auth.split(' ', 1)[1].strip()
    if Config.ADMIN_TOKEN and hmac.compare_digest(token, Config.ADMIN_TOKEN):
        return Principal(user_id=Config.ADMIN_USER_ID, role='admin')
    if Config.SERVICE_TOKEN and hmac.compare_digest(token, Config.SERVICE_TOKEN):
        return Principal(user_id=_asserted_user_id(), role='user')
    return None


def _attach(p):
    g.principal = p
    g.agent = request.headers.get('X-Agent')
    return p


def require_token(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        p = _resolve_principal()
        if p is None:
            return jsonify({'error': 'Missing or invalid Bearer token'}), 401
        _attach(p)
        return f(*args, **kwargs)
    return wrapped


def require_admin(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        p = _resolve_principal()
        if p is None or p.role != 'admin':
            return jsonify({'error': 'Admin token required'}), 403
        _attach(p)
        return f(*args, **kwargs)
    return wrapped


def require_admin_or_session(f):
    """Like require_admin but also accepts a valid admin UI session cookie."""
    from flask import session
    @wraps(f)
    def wrapped(*args, **kwargs):
        p = _resolve_principal()
        if p and p.role == 'admin':
            _attach(p)
            return f(*args, **kwargs)
        if session.get('admin'):
            g.principal = Principal(user_id=Config.ADMIN_USER_ID, role='admin')
            g.agent = request.headers.get('X-Agent')
            return f(*args, **kwargs)
        return jsonify({'error': 'Admin token or session required'}), 403
    return wrapped
