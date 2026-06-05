"""Nightly summary job — produces by-day and by-app aggregates.

Driven from `flask summary-job run`. Calls Config.MEMORY_FREE_MODELS in
order (cheap-first); failure of all models marks the job failed and does
not fall back to paid providers (cost control).
"""

from __future__ import annotations
import logging
from datetime import date, datetime, time, timezone, timedelta
from typing import Iterable, Tuple
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind, SummaryJob
from storage.memory import MemoryWriter
from storage.vault_renderer import VaultRenderer
from storage.sanitize import sanitize_for_summary

logger = logging.getLogger(__name__)


def run_for_day(target: date) -> list[SummaryJob]:
    """Aggregate per-user audit notes for the given calendar day (UTC).
    Returns one SummaryJob per user touched."""
    start = datetime.combine(target, time.min).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    user_ids = [
        row[0] for row in
        db.session.query(MemoryNote.user_id).filter(
            MemoryNote.kind == MemoryKind.AUDIT,
            MemoryNote.created_at >= start,
            MemoryNote.created_at < end,
            MemoryNote.deleted_at.is_(None),
        ).distinct().all()
    ]

    out = []
    for uid in user_ids:
        out.append(_run_one(period=f'day:{target.isoformat()}', user_id=uid,
                             start=start, end=end))
    return out


def run_for_app(target_app: str) -> list[SummaryJob]:
    """Aggregate per-(user, app) over the last 30 days. Returns one SummaryJob
    per user with audits for that app."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    user_ids = [
        row[0] for row in
        db.session.query(MemoryNote.user_id).filter(
            MemoryNote.kind == MemoryKind.AUDIT,
            MemoryNote.app == target_app,
            MemoryNote.created_at >= start,
            MemoryNote.created_at < end,
            MemoryNote.deleted_at.is_(None),
        ).distinct().all()
    ]
    out = []
    for uid in user_ids:
        out.append(_run_one(period=f'app:{target_app}', user_id=uid,
                             start=start, end=end, app_filter=target_app))
    return out


def _run_one(*, period: str, user_id: str, start: datetime, end: datetime,
             app_filter: str | None = None) -> SummaryJob:
    job = SummaryJob(period=period, user_id=user_id, status='running',
                     started_at=datetime.now(timezone.utc))
    db.session.add(job)
    db.session.commit()

    q = MemoryNote.query.filter(
        MemoryNote.user_id == user_id,
        MemoryNote.kind == MemoryKind.AUDIT,
        MemoryNote.created_at >= start,
        MemoryNote.created_at < end,
        MemoryNote.deleted_at.is_(None),
    )
    if app_filter:
        q = q.filter(MemoryNote.app == app_filter)
    notes = q.order_by(MemoryNote.created_at).all()
    cap = Config.SUMMARY_MAX_NOTES_PER_DAY

    try:
        if len(notes) > cap:
            body = (f'Skipped LLM summarization: {len(notes)} notes exceed cap of {cap}. '
                    f'Source ids: {[n.id for n in notes[:10]]}...')
            model_used = 'none'
        else:
            structured = _structure_notes(notes)
            if not Config.MEMORY_FREE_MODELS:
                raise RuntimeError('no free models configured')
            body, model_used = _call_model(period, structured, Config.MEMORY_FREE_MODELS)

        summary = MemoryWriter().write_summary(
            user_id=user_id, period=period, body=body,
            source_ids=[n.id for n in notes], model_used=model_used,
        )
        try:
            VaultRenderer().render_one(summary)
        except Exception as e:
            logger.warning(f'summary render failed: {e}')

        job.status = 'completed'
        job.model_used = model_used
    except Exception as e:
        job.status = 'failed'
        job.error_msg = f'{type(e).__name__}: {e}'[:1000]
    finally:
        job.finished_at = datetime.now(timezone.utc)
        db.session.commit()
    return job


def _structure_notes(notes: Iterable[MemoryNote]) -> str:
    lines = []
    for n in notes:
        ts = n.created_at.isoformat()
        prov = (n.extra or {}).get('provider', '?')
        title = sanitize_for_summary(n.title or '(no title)', 200)
        excerpt = sanitize_for_summary((n.body or '')[:300], 300)
        lines.append(f'- [{ts}] {n.app}/{prov}: {title} — {excerpt}')
    return '\n'.join(lines)


def _call_model(period: str, structured: str, models: list) -> Tuple[str, str]:
    """Call providers in order; return (summary_text, model_id_used).
    Raises RuntimeError if all fail."""
    from dispatcher import _execute
    prompt = (
        f'Summarize these chat events for {period}. '
        f'Return 5-10 sentences highlighting topics, decisions, and any unusual activity.\n\n'
        + structured
    )
    messages = [{'role': 'user', 'content': prompt}]
    last_err = None
    for spec in models:
        provider_id, _, model = spec.partition('::')
        if not provider_id or not model:
            continue
        try:
            result = _execute(
                user_id='__summary__', provider_id=provider_id, model=model,
                messages=messages, max_tokens=600,
                config_override={}, origin_app='memory-summarize',
            )
        except Exception as e:
            last_err = e
            continue
        text = ''
        if isinstance(result, dict):
            if isinstance(result.get('content'), str):
                text = result['content']
            elif isinstance(result.get('content'), list):
                text = '\n'.join(b.get('text', '') for b in result['content']
                                 if isinstance(b, dict))
        if text:
            return text.strip(), spec
        last_err = RuntimeError('empty response')
    raise RuntimeError(f'all free models failed: {last_err}')
