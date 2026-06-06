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

For the WebDAV bridge only, Basic Auth is additionally accepted (clients
like Obsidian Remotely Save and macOS Finder cannot send Bearer headers).
The Basic username becomes the user_id; the Basic password must match
SERVICE_TOKEN or ADMIN_TOKEN. See `require_token_or_basic` below.
"""

import base64
import binascii
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


def _resolve_basic_principal():
    """Parse `Authorization: Basic <base64(user:password)>` and validate.

    The Basic password must equal SERVICE_TOKEN or ADMIN_TOKEN. Basic username
    becomes the Principal.user_id directly — there is no separate user database.

    Returns None for any malformed input rather than raising.
    """
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Basic '):
        return None
    encoded = auth.split(' ', 1)[1].strip()
    try:
        decoded = base64.b64decode(encoded, validate=True).decode('utf-8')
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    if ':' not in decoded:
        return None
    user, _, password = decoded.partition(':')
    if Config.ADMIN_TOKEN and hmac.compare_digest(password, Config.ADMIN_TOKEN):
        # Admin token via Basic still scopes to the asserted user (Basic user
        # field). This is unusual but mirrors what `?user=` does for the
        # Bearer/admin path.
        return Principal(user_id=user or Config.ADMIN_USER_ID, role='admin')
    if Config.SERVICE_TOKEN and hmac.compare_digest(password, Config.SERVICE_TOKEN):
        if not user:
            return None
        return Principal(user_id=user, role='user')
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


_BASIC_REALM = 'ai-provider memory vault'


def require_token_or_basic(f):
    """Bearer (preferred) or Basic Auth. For use ONLY on the WebDAV bridge —
    do not extend Basic Auth to other endpoints without revisiting the threat
    model. 401 responses include `WWW-Authenticate: Basic` so WebDAV clients
    show a login prompt instead of failing silently.
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        p = _resolve_principal()
        if p is None:
            p = _resolve_basic_principal()
        if p is None:
            resp = jsonify({'error': 'authentication required'})
            resp.status_code = 401
            resp.headers['WWW-Authenticate'] = f'Basic realm="{_BASIC_REALM}"'
            return resp
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
