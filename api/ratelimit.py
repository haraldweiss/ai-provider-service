"""In-memory sliding-window rate limiter for API endpoints."""

from __future__ import annotations
import time
from collections import defaultdict
from flask import request, jsonify, g
from functools import wraps

_windows: dict[str, list[float]] = defaultdict(list)
_RATE_LIMITS: dict[str, tuple[int, int]] = {
    'memory:write': (60, 60),    # 60 POST/min per user
    'memory:read':  (120, 60),   # 120 GET/min per user
    'vault:export': (5, 60),     # 5 vault exports/min per user
}


def _key(bucket: str) -> str:
    uid = g.principal.user_id if hasattr(g, 'principal') else 'anonymous'
    return f'{bucket}:{uid}'


def _check(bucket: str, limit: int, window: int) -> bool:
    now = time.time()
    k = _key(bucket)
    q = _windows[k]
    cutoff = now - window
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def rate_limit(bucket: str):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if bucket not in _RATE_LIMITS:
                return f(*args, **kwargs)
            limit, window = _RATE_LIMITS[bucket]
            if not _check(bucket, limit, window):
                return jsonify({'error': 'rate limit exceeded'}), 429
            return f(*args, **kwargs)
        return wrapper
    return decorator
