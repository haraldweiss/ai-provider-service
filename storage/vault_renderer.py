"""VaultRenderer — projects DB rows to .md files under VAULT_PATH.

Source of truth is the DB. The vault directory is a rendered cache.
"""

from __future__ import annotations
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
import json
from config import Config
from database import db
from storage.memory_models import MemoryNote, MemoryKind

logger = logging.getLogger(__name__)


class VaultRenderer:
    """Stateless. All paths are derived from Config.VAULT_PATH per call so
    tests that monkeypatch the config see updates immediately."""

    def render_one(self, note: MemoryNote) -> Path:
        if note.deleted_at is not None:
            self._remove_one(note)
            return Path()
        path = self._path_for(note)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._compose_markdown(note), encoding='utf-8')
        return path

    def cleanup_deleted(self) -> int:
        removed = 0
        deleted = (MemoryNote.query
                   .filter(MemoryNote.deleted_at.isnot(None))
                   .all())
        for n in deleted:
            if self._remove_one(n):
                removed += 1
        return removed

    def rebuild_all(self, user_id: Optional[str] = None) -> int:
        q = MemoryNote.query.filter(MemoryNote.deleted_at.is_(None))
        if user_id:
            q = q.filter_by(user_id=user_id)
        count = 0
        for n in q.all():
            self.render_one(n)
            count += 1
        return count

    def check_stale(self) -> int:
        count = 0
        for n in MemoryNote.query.filter(MemoryNote.deleted_at.is_(None)).all():
            path = self._path_for(n)
            if not path.exists():
                self.render_one(n)
                count += 1
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                self.render_one(n)
                count += 1
                continue
            db_updated = n.updated_at
            if db_updated.tzinfo is None:
                db_updated = db_updated.replace(tzinfo=timezone.utc)
            if db_updated > mtime:
                self.render_one(n)
                count += 1
        # The self-heal pass also removes orphan .md files (files on disk
        # without a matching live DB row). This catches hand-written or
        # leftover-from-deleted-app files that would otherwise accumulate.
        self.cleanup_orphans()
        return count

    def cleanup_orphans(self, user_id: Optional[str] = None) -> int:
        """Remove `.md` files in the vault that have no matching live DB row.

        Walks `VAULT_PATH/<user>/...` (or only `<user_id>` if given). For each
        `.md` file, derives `(user, folder, slug)` from the path and checks
        against live (non-soft-deleted) `MemoryNote` rows. Files with no
        matching row are deleted. Returns count removed.

        Non-`.md` files are ignored on purpose so users can keep `.obsidian/`
        configs and other side-files in the vault tree.
        """
        root = Path(Config.VAULT_PATH)
        if not root.exists():
            return 0

        # Build the set of live (user, folder, slug) tuples once.
        q = (db.session.query(MemoryNote.user_id,
                              MemoryNote.folder,
                              MemoryNote.slug)
             .filter(MemoryNote.deleted_at.is_(None)))
        if user_id:
            q = q.filter(MemoryNote.user_id == user_id)
        alive = {(u, f, s) for u, f, s in q.all()}

        # Decide which user subtree(s) to scan.
        if user_id:
            target = root / user_id
            user_roots = [target] if target.exists() and target.is_dir() else []
        else:
            user_roots = [d for d in root.iterdir() if d.is_dir()]

        removed = 0
        for user_root in user_roots:
            u = user_root.name
            for md in user_root.rglob('*.md'):
                if not md.is_file():
                    continue
                rel = md.relative_to(user_root)
                folder = str(rel.parent).replace(os.sep, '/')
                slug = md.stem
                if (u, folder, slug) not in alive:
                    try:
                        md.unlink()
                        removed += 1
                    except OSError as e:
                        logger.warning(f'vault orphan unlink failed for {md}: {e}')
        return removed

    def _path_for(self, note: MemoryNote) -> Path:
        root = Path(Config.VAULT_PATH)
        return root / note.user_id / note.folder / f'{note.slug}.md'

    def _remove_one(self, note: MemoryNote) -> bool:
        path = self._path_for(note)
        if path.exists():
            try:
                path.unlink()
                return True
            except OSError as e:
                logger.warning(f'vault unlink failed for {path}: {e}')
        return False

    def _compose_markdown(self, note: MemoryNote) -> str:
        frontmatter = {
            'id': note.id,
            'kind': note.kind.value,
            'user': note.user_id,
            'app': note.app,
            'created': (note.created_at.replace(tzinfo=timezone.utc)
                        if note.created_at.tzinfo is None
                        else note.created_at).isoformat(),
            'tags': note.tags or [],
        }
        if note.extra:
            for k, v in note.extra.items():
                frontmatter[k] = v
        if note.chat_request_id:
            frontmatter['chat_request_id'] = note.chat_request_id

        fm_lines = ['---']
        for k, v in frontmatter.items():
            fm_lines.append(f'{k}: {json.dumps(v, ensure_ascii=False)}')
        fm_lines.append('---')
        fm = '\n'.join(fm_lines)

        title_line = f'\n# {note.title}\n' if note.title else ''
        return f'{fm}\n{title_line}\n{note.body}\n'
