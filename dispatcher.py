"""Request-Dispatcher: orchestriert Primary → Fallback → Queue.

Das ist der Kern des Services: jeder /chat-Aufruf läuft hier durch.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from database import db
from storage.models import ProviderConfig, RequestQueue
from providers import get_client, PROVIDER_REGISTRY
import health_tracker

logger = logging.getLogger(__name__)


def _load_config(user_id: str, provider_id: str) -> dict | None:
    """Lädt + entschlüsselt Provider-Config aus DB. Für System-Provider (Claude,
    Ollama) leeres Dict, falls keine User-Config existiert."""
    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
    if pc:
        return pc.get_config()
    if PROVIDER_REGISTRY.get(provider_id, {}).get('system'):
        return {}
    return None


def _execute(user_id: str, provider_id: str, model: str, messages: list, max_tokens: int) -> dict:
    """Führt einen Request synchron aus. Updated Health-Status."""
    cfg = _load_config(user_id, provider_id)
    if cfg is None:
        raise ValueError(f"Provider {provider_id} ist nicht konfiguriert für user_id={user_id}")

    client = get_client(provider_id, cfg)
    try:
        result = client.create_message(model, messages, max_tokens)
        health_tracker.set_status(provider_id, True)
        return result
    except Exception as e:
        health_tracker.set_status(provider_id, False, reason=f"{type(e).__name__}: {e}")
        raise


def dispatch(
    user_id: str,
    provider_id: str,
    model: str,
    messages: list,
    max_tokens: int = 600,
) -> dict:
    """Hauptmethode. Verhalten:

    1. Primary erreichbar (oder optimistisch unbekannt) → execute
    2. Primary down + Fallback konfiguriert → execute via Fallback
    3. Primary down + queue_when_unavailable=True → in Queue
    4. Sonst → Fehler werfen
    """
    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
    fallback = pc.fallback_provider if pc else None
    should_queue = pc.queue_when_unavailable if pc else False
    queue_ttl_h = pc.queue_ttl_hours if pc else 24

    primary_healthy = health_tracker.is_healthy(provider_id)

    # 1) Primary versuchen, wenn als healthy bekannt (oder unbekannt → optimistisch)
    if primary_healthy:
        try:
            result = _execute(user_id, provider_id, model, messages, max_tokens)
            return {'result': result, 'via': provider_id, 'fallback_used': False}
        except Exception as e:
            logger.info(f'Primary {provider_id} failed for user={user_id}: {e}')
            # weiter mit Fallback / Queue

    # 2) Fallback versuchen
    if fallback:
        try:
            logger.info(f'Trying fallback {fallback} for user={user_id}')
            result = _execute(user_id, fallback, model, messages, max_tokens)
            return {
                'result': result, 'via': fallback,
                'fallback_used': True, 'primary_provider': provider_id,
            }
        except Exception as e:
            logger.warning(f'Fallback {fallback} also failed for user={user_id}: {e}')

    # 3) Queueing
    if should_queue:
        q = RequestQueue(
            id=str(uuid.uuid4()),
            user_id=user_id,
            primary_provider=provider_id,
            payload=json.dumps({
                'provider': provider_id, 'model': model,
                'messages': messages, 'max_tokens': max_tokens,
            }),
            status='pending',
            expires_at=datetime.utcnow() + timedelta(hours=queue_ttl_h),
        )
        db.session.add(q)
        db.session.commit()
        return {
            'queued': True, 'queue_id': q.id,
            'primary_provider': provider_id,
            'expires_at': q.expires_at.isoformat(),
        }

    # 4) Kein Fallback, kein Queueing → Fehler
    raise RuntimeError(
        f"Provider {provider_id} nicht erreichbar, kein Fallback/Queue konfiguriert"
    )


def drain_queue_for_provider(provider_id: str, max_items: int = 50) -> dict:
    """Verarbeitet pending Queue-Einträge für einen wieder erreichbaren Provider.
    Wird vom Worker aufgerufen, sobald Provider von down → up wechselt."""
    now = datetime.utcnow()
    pending = (RequestQueue.query
               .filter_by(primary_provider=provider_id, status='pending')
               .filter(RequestQueue.expires_at > now)
               .order_by(RequestQueue.created_at)
               .limit(max_items).all())

    processed = 0
    failed = 0
    for q in pending:
        q.status = 'processing'
        q.attempts = (q.attempts or 0) + 1
        db.session.commit()

        payload = q.get_payload()
        try:
            result = _execute(
                q.user_id, q.primary_provider,
                payload.get('model'), payload.get('messages', []),
                payload.get('max_tokens', 600),
            )
            q.result = json.dumps({'result': result, 'via': q.primary_provider, 'fallback_used': False})
            q.status = 'done'
            q.completed_at = now
            processed += 1
        except Exception as e:
            q.last_error = f"{type(e).__name__}: {str(e)[:500]}"
            q.status = 'pending'  # bei nächstem Drain wieder versuchen
            failed += 1
        db.session.commit()

    # Expired markieren
    expired = RequestQueue.query.filter(
        RequestQueue.status == 'pending',
        RequestQueue.expires_at <= now,
    ).all()
    for q in expired:
        q.status = 'expired'
    if expired:
        db.session.commit()

    return {'processed': processed, 'failed': failed, 'expired': len(expired)}
