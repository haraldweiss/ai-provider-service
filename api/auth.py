"""Bearer-Token-Auth (Shared Secret aus SERVICE_TOKEN)."""

from functools import wraps
from flask import request, jsonify
from config import Config


def require_token(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'error': 'Missing Bearer token'}), 401
        token = auth.split(' ', 1)[1].strip()
        if token != Config.SERVICE_TOKEN:
            return jsonify({'error': 'Invalid token'}), 401
        return f(*args, **kwargs)
    return wrapped
