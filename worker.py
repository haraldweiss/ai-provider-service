"""Background-Worker: Health-Check polling + Queue-Drain.

Läuft als Daemon-Thread im Flask-Prozess. Bei Multi-Worker-Gunicorn pollt
jeder Worker eigenständig — das ist OK, weil:
- Health-Status ist in-memory pro Worker (keine globale Konsistenz nötig).
- Queue-Drain ist DB-basiert + transactional (RequestQueue.status='processing'
  wird sofort committed, andere Worker überspringen den Eintrag).
"""

from __future__ import annotations
import logging
import threading
import time
from typing import Optional
from flask import Flask
from config import Config
from providers import get_client, PROVIDER_REGISTRY
from storage.models import ProviderConfig
from database import db
import health_tracker
from dispatcher import drain_queue_for_provider

logger = logging.getLogger(__name__)

_worker_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()


def _check_provider(provider_id: str) -> bool:
    """Health-Check für einen Provider. Nutzt eine beliebige user-Config oder
    System-Defaults."""
    cfg = {}
    # Wenn user-Provider, brauchen wir eine Config — nimm die erste verfügbare.
    if not PROVIDER_REGISTRY[provider_id]['system']:
        pc = ProviderConfig.query.filter_by(provider_id=provider_id).first()
        if not pc:
            # Kein User hat diesen Provider konfiguriert → Health irrelevant.
            return True
        cfg = pc.get_config()

    try:
        client = get_client(provider_id, cfg)
        ok = client.health()
        health_tracker.set_status(provider_id, ok, reason='' if ok else 'health_check_failed')
        return ok
    except Exception as e:
        health_tracker.set_status(provider_id, False, reason=f'{type(e).__name__}: {e}')
        return False


def _tick(app: Flask) -> None:
    """Ein Worker-Durchlauf: alle Provider pingen + bei Recovery Queue drainen."""
    with app.app_context():
        for pid in PROVIDER_REGISTRY:
            try:
                _check_provider(pid)
            except Exception as e:
                logger.warning(f'health-check {pid} crashed: {e}')


def _drain(app: Flask) -> None:
    """Separater Durchlauf: Queue-Drain für alle recovered Provider."""
    with app.app_context():
        for pid in PROVIDER_REGISTRY:
            if health_tracker.just_recovered(pid):
                logger.info(f'{pid} recovered → draining queue')
                try:
                    res = drain_queue_for_provider(pid)
                    logger.info(f'drain {pid}: {res}')
                except Exception as e:
                    logger.warning(f'drain {pid} crashed: {e}')


def _run(app: Flask) -> None:
    hc_interval = Config.HEALTH_CHECK_INTERVAL_SEC
    qd_interval = Config.QUEUE_DRAIN_INTERVAL_SEC
    sleep_sec = min(hc_interval, qd_interval)
    logger.info(f'Worker startet, health-check={hc_interval}s, drain={qd_interval}s, sleep={sleep_sec}s')
    tick = 0
    while not _stop_event.is_set():
        try:
            if tick % (hc_interval // sleep_sec) == 0:
                _tick(app)
        except Exception as e:
            logger.exception(f'tick crashed: {e}')
        try:
            if tick % (qd_interval // sleep_sec) == 0:
                _drain(app)
        except Exception as e:
            logger.exception(f'drain crashed: {e}')
        tick += 1
        for _ in range(sleep_sec):
            if _stop_event.is_set():
                break
            time.sleep(1)


def start(app: Flask) -> None:
    """Idempotent — startet Worker einmal."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_run, args=(app,), daemon=True, name='ai-provider-worker')
    _worker_thread.start()


def stop() -> None:
    _stop_event.set()
