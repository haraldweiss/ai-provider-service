"""Async TTL cache für die /v1/models-Liste, keyed pro user_id.

Die teure Arbeit bei GET /v1/models ist das Netzwerk-Bauen: pro Provider
`get_models()`-Calls (Ollama-Pool-Union über mehrere Macs + Remote-Gateways).
Dieser Cache hält die fertig gebauten Rows pro user_id mit TTL. Der
Background-Worker (worker.py) refresht proaktiv alle bereits gecachten User,
damit der nächste Request ein Hit ist.

Bei einem Cold-Miss (User noch nie gefragt) baut der Request-Thread synchron —
so ist der erste Request immer korrekt, danach bedient der Cache.

Multi-Worker-Hinweis: wie health_tracker hat jeder Gunicorn-Worker seinen
eigenen Cache. Für /v1/models ist das unkritisch (Listen-Daten, keine
Consistenz-Anforderung wie beim Queue-Drain).
"""

from __future__ import annotations
import logging
import threading
import time
from typing import Dict, List, Optional

from config import Config

logger = logging.getLogger(__name__)

_cache: Dict[str, dict] = {}
_lock = threading.Lock()


def _ttl() -> int:
    return max(1, int(getattr(Config, 'MODEL_CACHE_TTL_SEC', 60)))


def get(user_id: str) -> Optional[List[dict]]:
    """Cached-Rows für user_id, oder None wenn fehlend/abgelaufen."""
    with _lock:
        entry = _cache.get(user_id)
        if not entry:
            return None
        if time.time() >= entry['expires']:
            return None
        return entry['rows']


def put(user_id: str, rows: List[dict]) -> None:
    with _lock:
        _cache[user_id] = {'rows': rows, 'expires': time.time() + _ttl()}


def invalidate(user_id: Optional[str] = None) -> None:
    """Löscht den Cache für einen user_id, oder alle wenn user_id=None."""
    with _lock:
        if user_id is None:
            _cache.clear()
        else:
            _cache.pop(user_id, None)


def cached_user_ids() -> List[str]:
    """Alle tracked user_ids (auch abgelaufene) — der Worker refresht genau diese."""
    with _lock:
        return list(_cache.keys())
