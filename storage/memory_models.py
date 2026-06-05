"""ORM models for markdown memory: MemoryNote (polymorphic) + SummaryJob."""

from __future__ import annotations
import enum
from datetime import datetime, timezone
from database import db


class MemoryKind(str, enum.Enum):
    AUDIT = 'audit'
    NOTE = 'note'
    EVENT = 'event'
    SUMMARY = 'summary'


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryNote(db.Model):
    """Polymorphic by `kind`. One table covers audit notes, app-written notes,
    typed events, and LLM-generated summaries. See
    docs/superpowers/specs/2026-06-05-markdown-memory-design.md for layout."""
    __tablename__ = 'memory_notes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(255), nullable=False)
    app = db.Column(db.String(64), nullable=False)
    kind = db.Column(db.Enum(MemoryKind), nullable=False)
    folder = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(120), nullable=False)
    title = db.Column(db.String(255), nullable=False, default='')
    body = db.Column(db.Text, nullable=False, default='')
    tags = db.Column(db.JSON, nullable=False, default=list)
    extra = db.Column(db.JSON, nullable=False, default=dict)
    chat_request_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    deleted_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'folder', 'slug', name='uq_memory_user_folder_slug'),
        db.Index(
            'uq_memory_chat_request_id', 'chat_request_id',
            unique=True, sqlite_where=db.text('chat_request_id IS NOT NULL'),
        ),
        db.Index('ix_memory_user_kind', 'user_id', 'kind'),
        db.Index('ix_memory_user_folder', 'user_id', 'folder'),
        db.Index('ix_memory_user_created', 'user_id', 'created_at'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'app': self.app,
            'kind': self.kind.value,
            'folder': self.folder,
            'slug': self.slug,
            'title': self.title,
            'body': self.body,
            'tags': self.tags or [],
            'extra': self.extra or {},
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'deleted_at': self.deleted_at.isoformat() if self.deleted_at else None,
        }


class SummaryJob(db.Model):
    """Tracks one execution of the nightly summary job per (period, user)."""
    __tablename__ = 'summary_jobs'

    id = db.Column(db.Integer, primary_key=True)
    period = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='pending')
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    error_msg = db.Column(db.Text, nullable=True)
    model_used = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        db.Index('ix_summary_user_period', 'user_id', 'period'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'period': self.period,
            'user_id': self.user_id,
            'status': self.status,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'error_msg': self.error_msg,
            'model_used': self.model_used,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
