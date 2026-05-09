"""In-memory Health-Tracker für Provider-Erreichbarkeit.

Statt bei jedem Request einen Health-Check zu machen, cachen wir den Status
für eine kurze Zeit (Config.HEALTH_CHECK_INTERVAL_SEC). Background-Worker
aktualisiert ihn regelmäßig.

Multi-Worker-Hinweis: Bei mehreren Gunicorn-Workern hat jeder Worker seinen
eigenen Cache. Für stabile Recovery-Detection (Queue-Drain) reicht das aus,
weil der Worker alle paar Sekunden neu pollt.
"""

from __future__ import annotations
import threading
import time
from typing import Dict
from config import Config

_status: Dict[str, dict] = {}
_lock = threading.Lock()


def set_status(provider_id: str, healthy: bool, reason: str = '') -> None:
    with _lock:
        prev = _status.get(provider_id, {}).get('healthy')
        _status[provider_id] = {
            'healthy': healthy,
            'reason': reason,
            'updated_at': time.time(),
            'previous_healthy': prev,
        }


def get_status(provider_id: str) -> dict:
    with _lock:
        s = _status.get(provider_id)
        if not s:
            return {'healthy': None, 'updated_at': 0, 'reason': 'not_checked'}
        # Stale-Check: wenn Status zu alt, gilt er als unbekannt.
        if time.time() - s['updated_at'] > Config.HEALTH_CHECK_INTERVAL_SEC * 4:
            return {'healthy': None, 'updated_at': s['updated_at'], 'reason': 'stale'}
        return dict(s)


def is_healthy(provider_id: str) -> bool:
    """True wenn Provider als gesund gilt (oder unbekannt = optimistisch).

    Bei `unbekannt` (z.B. erster Aufruf) → optimistisch True. Wenn der Call
    dann fehlschlägt, korrigiert sich der Status automatisch.
    """
    s = get_status(provider_id)
    if s['healthy'] is None:
        return True
    return bool(s['healthy'])


def all_status() -> dict:
    with _lock:
        return {pid: dict(s) for pid, s in _status.items()}


def just_recovered(provider_id: str) -> bool:
    """True wenn der letzte Status-Wechsel von down → up war (für Queue-Drain-Trigger)."""
    s = get_status(provider_id)
    return s.get('healthy') is True and s.get('previous_healthy') is False
