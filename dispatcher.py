"""Request-Dispatcher: orchestriert Primary → Fallback → Queue.

Das ist der Kern des Services: jeder /chat-Aufruf läuft hier durch.
"""

from __future__ import annotations
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from database import db
from storage.models import ProviderConfig, RequestQueue
from providers import get_client, PROVIDER_REGISTRY
import health_tracker

logger = logging.getLogger(__name__)


def _is_claude_server_key_allowed(user_id: str) -> bool:
    """Allowlist für den zentralen ANTHROPIC_API_KEY (Server-Key).

    Steuert, welche User-IDs den server-seitigen Claude-Key benutzen dürfen.
    Wenn `CLAUDE_SERVER_KEY_ALLOWED_USERS` leer/nicht gesetzt ist, ist der
    Server-Key OFFEN (Backward-Compat / single-tenant Setups).
    Wenn gesetzt, dürfen NUR die gelisteten user_ids den Server-Key nutzen,
    alle anderen müssen einen eigenen Claude-API-Key konfigurieren.
    """
    allowed_raw = os.getenv('CLAUDE_SERVER_KEY_ALLOWED_USERS', '').strip()
    if not allowed_raw:
        return True  # offen — wie bisher
    allowed = {u.strip() for u in allowed_raw.split(',') if u.strip()}
    return user_id in allowed


def _load_config(user_id: str, provider_id: str) -> Optional[dict]:
    """Lädt + entschlüsselt Provider-Config aus DB. Für System-Provider (Claude,
    Ollama) leeres Dict, falls keine User-Config existiert.

    Sonderfall Claude: der zentrale ANTHROPIC_API_KEY ist nur für allowlistete
    User verfügbar — andere müssen ihren eigenen Key konfigurieren oder einen
    anderen Provider wählen.

    Sonderfall opencode: Free-Modelle laufen über den zentralen OPENCODE_API_KEY
    (ohne eigene Konfiguration). Paid-Modelle brauchen einen eigenen API-Key.
    """
    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
    if pc:
        return pc.get_config()
    if PROVIDER_REGISTRY.get(provider_id, {}).get('system'):
        if provider_id == 'claude' and not _is_claude_server_key_allowed(user_id):
            return None
        return {}
    # Non-system provider: opencode free models via system key
    if provider_id == 'opencode' and Config.OPENCODE_API_KEY:
        return {'_free_only': True, 'api_key': Config.OPENCODE_API_KEY}
    return None


def _log_usage_event(
    user_id: str, provider_id: str, model: str,
    input_tokens, output_tokens, status: str,
    error_message: Optional[str] = None,
    origin_app: Optional[str] = None,
) -> None:
    """Schreibt einen UsageEvent. Logging-Fehler werden geschluckt — der
    Hot-Path darf dadurch nicht abbrechen."""
    try:
        from pricing import calc_cost_usd
        from storage.models import UsageEvent
        cost = calc_cost_usd(provider_id, model, input_tokens, output_tokens)
        ev = UsageEvent(
            user_id=user_id, provider_id=provider_id, model=model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_usd=cost, origin_app=origin_app, status=status,
            error_message=error_message,
        )
        db.session.add(ev)
        db.session.commit()
    except Exception as log_err:
        logger.warning(f'usage_event logging failed: {log_err}')
        db.session.rollback()


def _write_audit_note(
    user_id: str, provider_id: str, origin_app: Optional[str],
    chat_request_id: str, prompt_text: str, response_text: str,
    usage: dict, cost_eur: float, latency_ms: int,
) -> None:
    """Write an audit note via MemoryWriter. Failures are swallowed — audit
    must never break a chat (see spec Flow 1)."""
    from config import Config as _Config
    if not _Config.MEMORY_ENABLED:
        return
    try:
        from storage.memory import MemoryWriter, NoteAlreadyExists
        try:
            MemoryWriter().write_audit(
                user_id=user_id,
                app=origin_app or 'gateway',
                provider=provider_id,
                chat_request_id=chat_request_id,
                prompt=prompt_text,
                response=response_text,
                tokens=usage or {},
                cost_eur=cost_eur,
                latency_ms=latency_ms,
                timestamp=None,
            )
        except NoteAlreadyExists:
            return
    except Exception as e:
        logger.warning(f'memory audit write failed: {e}')
        try:
            db.session.rollback()
        except Exception:
            pass


def _join_messages(messages: list) -> str:
    """Concatenate user/assistant/system messages into a single prompt string
    for audit storage."""
    lines = []
    for m in messages or []:
        role = m.get('role', '?')
        content = m.get('content', '')
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get('text', '') or '')
                else:
                    parts.append(str(block))
            content = '\n'.join(parts)
        lines.append(f'**{role}**\n{content}')
    return '\n\n'.join(lines)


def _extract_response_text(result: dict) -> str:
    """Pull the assistant response text out of a provider result dict."""
    if not isinstance(result, dict):
        return str(result)
    if isinstance(result.get('content'), str):
        return result['content']
    if isinstance(result.get('content'), list):
        return '\n'.join(
            b.get('text', '') for b in result['content'] if isinstance(b, dict)
        )
    return result.get('text') or result.get('message') or ''


def _execute(
    user_id: str, provider_id: str, model: str, messages: list, max_tokens: int,
    config_override: Optional[dict] = None,
    origin_app: Optional[str] = None,
    tools: Optional[list] = None,
) -> dict:
    """Führt einen Request synchron aus. Updated Health-Status und schreibt
    UsageEvent (success + error).

    `config_override` (optional) ersetzt die DB-Config — nützlich für Per-Request
    Fallback-Configs, die nicht persistiert werden sollen (z.B. Server-Key des Admins).
    `origin_app` ist der optionale `X-Origin-App` Header-Wert für Usage-Tracking.
    `tools` (optional) — list of tool-definition dicts, durchgereicht an Provider
    mit Tool-Use-Support (Claude). Provider ohne Support ignorieren stillschweigend.
    """
    cfg = config_override if config_override is not None else _load_config(user_id, provider_id)
    if cfg is None:
        raise ValueError(f"Provider {provider_id} ist nicht konfiguriert für user_id={user_id}")

    client = get_client(provider_id, cfg)
    chat_request_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc)
    try:
        result = client.create_message(model, messages, max_tokens, tools=tools)
        latency_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        health_tracker.set_status(provider_id, True)
        usage = (result or {}).get('usage') or {}
        _log_usage_event(
            user_id, provider_id, model,
            usage.get('input_tokens'), usage.get('output_tokens'),
            'success', origin_app=origin_app,
        )
        prompt_text = _join_messages(messages)
        response_text = _extract_response_text(result)
        try:
            _write_audit_note(
                user_id=user_id, provider_id=provider_id, origin_app=origin_app,
                chat_request_id=chat_request_id,
                prompt_text=prompt_text, response_text=response_text,
                usage=usage, cost_eur=0.0, latency_ms=latency_ms,
            )
        except Exception:
            logger.warning('memory audit write failed in _execute', exc_info=True)
        return result
    except Exception as e:
        health_tracker.set_status(provider_id, False, reason=f"{type(e).__name__}: {e}")
        _log_usage_event(
            user_id, provider_id, model, None, None,
            'error', error_message=f"{type(e).__name__}: {e}",
            origin_app=origin_app,
        )
        raise


def dispatch(
    user_id: str,
    provider_id: str,
    model: str,
    messages: list,
    max_tokens: int = 600,
    *,
    fallback_provider_override: Optional[str] = None,
    fallback_model_override: Optional[str] = None,
    fallback_config_override: Optional[dict] = None,
    origin_app: Optional[str] = None,
    tools: Optional[list] = None,
) -> dict:
    """Hauptmethode. Verhalten:

    1. Primary erreichbar (oder optimistisch unbekannt) → execute
    2. Primary down + Fallback konfiguriert → execute via Fallback
    3. Primary down + queue_when_unavailable=True → in Queue
    4. Sonst → Fehler werfen

    Fallback-Quellen (Priorität): Per-Request-Override > DB-ProviderConfig.fallback_provider.
    Per-Request-Override erlaubt Clients (z.B. Bewerbungstracker) ihre eigene
    Fallback-Strategie pro Aufruf zu übergeben, ohne sie in der Service-DB zu
    persistieren.
    """
    pc = ProviderConfig.query.filter_by(user_id=user_id, provider_id=provider_id).first()
    should_queue = pc.queue_when_unavailable if pc else False
    queue_ttl_h = pc.queue_ttl_hours if pc else 24

    # Effektiver Fallback: Per-Request-Override gewinnt vor DB-Config
    if fallback_provider_override:
        fallback = fallback_provider_override
        fallback_model = fallback_model_override or model
        fallback_cfg = fallback_config_override  # None → _load_config aus DB
    else:
        fallback = pc.fallback_provider if pc else None
        fallback_model = model
        fallback_cfg = None

    primary_healthy = health_tracker.is_healthy(provider_id)

    # 1) Primary versuchen, wenn als healthy bekannt (oder unbekannt → optimistisch)
    if primary_healthy:
        try:
            result = _execute(user_id, provider_id, model, messages, max_tokens,
                              origin_app=origin_app, tools=tools)
            return {
                'result': result, 'via': provider_id, 'model': model,
                'fallback_used': False,
            }
        except Exception as e:
            logger.info(f'Primary {provider_id} failed for user={user_id}: {e}')
            # weiter mit Fallback / Queue

    # 2) Fallback versuchen
    if fallback:
        try:
            logger.info(f'Trying fallback {fallback} (model={fallback_model}) for user={user_id}')
            result = _execute(user_id, fallback, fallback_model, messages, max_tokens,
                              fallback_cfg, origin_app=origin_app, tools=tools)
            return {
                'result': result, 'via': fallback, 'model': fallback_model,
                'fallback_used': True, 'primary_provider': provider_id,
                'primary_model': model,
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
            expires_at=datetime.now(timezone.utc) + timedelta(hours=queue_ttl_h),
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
    now = datetime.now(timezone.utc)

    # Stale 'processing' items recover: if a previous drain crashed after
    # marking an item 'processing' but before completing it, reset to
    # 'pending' so it gets picked up now. 30 min = safe upper bound for
    # any provider call. Uses created_at since we lack an updated_at column.
    stale = (RequestQueue.query
             .filter_by(primary_provider=provider_id, status='processing')
             .filter(RequestQueue.expires_at > now)
             .filter(RequestQueue.created_at < now - timedelta(minutes=30))
             .all())
    for q in stale:
        q.status = 'pending'
    if stale:
        db.session.commit()

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
                origin_app=None,  # Queued requests lose origin until payload schema extends.
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
