"""MemoryWriter — single entry point for inserting memory_notes rows.

Each public method commits a single row. Filesystem rendering is a separate
concern (see storage.vault_renderer).
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from database import db
from storage.memory_models import MemoryNote, MemoryKind
from storage.slug import slugify, validate_explicit_slug, next_free_slug


class NoteAlreadyExists(Exception):
    """Raised when the unique constraint on (user, folder, slug) or on
    chat_request_id rejects the insert. Carries the existing note id."""

    def __init__(self, existing_id: int, msg: str = ''):
        super().__init__(msg or f'note already exists (id={existing_id})')
        self.existing_id = existing_id


class MemoryWriter:
    """All writes funnel through here. Stateless — instantiate per request or
    keep one instance on the app, both work."""

    def write_note(self, *, user_id: str, app: str, title: str, body: str,
                   tags: list, folder: Optional[str], slug: Optional[str]) -> MemoryNote:
        target_folder = self._resolve_note_folder(app, folder)
        chosen_slug = self._choose_slug(user_id, target_folder, title, slug)
        n = MemoryNote(
            user_id=user_id, app=app, kind=MemoryKind.NOTE,
            folder=target_folder, slug=chosen_slug,
            title=title, body=body, tags=tags or [], extra={},
        )
        db.session.add(n)
        db.session.commit()
        return n

    def write_audit(self, *, user_id: str, app: str, provider: str,
                    chat_request_id: str, prompt: str, response: str,
                    tokens: dict, cost_eur: float, latency_ms: int,
                    timestamp: Optional[datetime]) -> MemoryNote:
        existing = (MemoryNote.query
                    .filter_by(chat_request_id=chat_request_id)
                    .first())
        if existing is not None:
            raise NoteAlreadyExists(existing.id, f'audit for {chat_request_id} exists')

        ts = timestamp or datetime.now(timezone.utc)
        folder = f'{app}/audit/{ts.year:04d}/{ts.month:02d}/{ts.day:02d}'
        slug_base = ts.strftime('%Y%m%dT%H%M%SZ') + '-' + chat_request_id[:12]
        chosen_slug = self._next_free(user_id, folder, slug_base)

        body = f'## Prompt\n\n{prompt}\n\n## Response\n\n{response}\n'
        n = MemoryNote(
            user_id=user_id, app=app, kind=MemoryKind.AUDIT,
            folder=folder, slug=chosen_slug,
            title='', body=body, tags=['chat', 'audit'],
            extra={
                'provider': provider,
                'chat_request_id': chat_request_id,
                'tokens': tokens,
                'cost_eur': cost_eur,
                'latency_ms': latency_ms,
            },
            chat_request_id=chat_request_id,
        )
        db.session.add(n)
        db.session.commit()
        return n

    def write_event(self, *, user_id: str, app: str, event_type: str,
                    payload: dict, tags: list, slug: Optional[str]) -> MemoryNote:
        folder = f'{app}/events/{event_type}'
        chosen_slug = self._choose_slug(user_id, folder, event_type, slug)
        body = f'```json\n{_json_dumps(payload)}\n```\n'
        n = MemoryNote(
            user_id=user_id, app=app, kind=MemoryKind.EVENT,
            folder=folder, slug=chosen_slug,
            title=event_type, body=body, tags=tags or [],
            extra={'event_type': event_type, 'payload': payload},
        )
        db.session.add(n)
        db.session.commit()
        return n

    def write_summary(self, *, user_id: str, period: str, body: str,
                      source_ids: list, model_used: str) -> MemoryNote:
        kind_label, _, value = period.partition(':')
        if kind_label == 'day':
            folder = '_index/by-day'
            slug = value
        elif kind_label == 'app':
            folder = '_index/by-app'
            slug = value
        else:
            raise ValueError(f'unknown period: {period}')

        existing = (MemoryNote.query
                    .filter_by(user_id=user_id, folder=folder, slug=slug,
                               kind=MemoryKind.SUMMARY)
                    .first())
        if existing is not None:
            existing.body = body
            existing.extra = {'source_ids': source_ids, 'model': model_used,
                              'period': period}
            db.session.commit()
            return existing

        n = MemoryNote(
            user_id=user_id, app='gateway', kind=MemoryKind.SUMMARY,
            folder=folder, slug=slug,
            title=period, body=body, tags=['summary'],
            extra={'source_ids': source_ids, 'model': model_used, 'period': period},
        )
        db.session.add(n)
        db.session.commit()
        return n

    def _resolve_note_folder(self, app: str, folder: Optional[str]) -> str:
        if folder is None or folder == '':
            return f'{app}/notes'
        if folder == '_shared':
            return '_shared/notes'
        if not folder.startswith(f'{app}/'):
            raise ValueError(
                f'folder must start with "{app}/" or be "_shared" (got {folder!r})'
            )
        return folder

    def _choose_slug(self, user_id: str, folder: str, title: str,
                     explicit: Optional[str]) -> str:
        if explicit is not None:
            if not validate_explicit_slug(explicit):
                raise ValueError(f'invalid slug: {explicit!r}')
            base = explicit
        else:
            base = slugify(title)
        return self._next_free(user_id, folder, base)

    def _next_free(self, user_id: str, folder: str, base: str) -> str:
        existing = {
            row.slug for row in
            db.session.query(MemoryNote.slug).filter(
                MemoryNote.user_id == user_id,
                MemoryNote.folder == folder,
            ).all()
        }
        return next_free_slug(base, existing)


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, indent=2, ensure_ascii=False)
