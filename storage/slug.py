"""Slug helpers for markdown memory filenames."""

from __future__ import annotations
import re
import unicodedata

_SLUG_MAX_LEN = 80
_ALLOWED_RE = re.compile(r'^[a-z0-9-]{1,80}$')


def slugify(title: str) -> str:
    """Convert free-form title to filesystem-safe slug.

    Falls back to 'note' for empty / pure-special-char input.
    Truncates to 80 chars.
    """
    if not title:
        return 'note'
    normalized = unicodedata.normalize('NFKD', title)
    ascii_only = ''.join(c for c in normalized if not unicodedata.combining(c))
    lowered = ascii_only.lower()
    s = re.sub(r'[^a-z0-9]+', '-', lowered).strip('-')
    if not s:
        return 'note'
    return s[:_SLUG_MAX_LEN]


def validate_explicit_slug(slug: str) -> bool:
    """True if `slug` matches the strict pattern an app may submit."""
    return bool(_ALLOWED_RE.match(slug or ''))


def next_free_slug(base: str, taken: set[str]) -> str:
    """Return `base`, or `base-2`/`base-3`/... if taken. Max 100 attempts."""
    if base not in taken:
        return base
    for i in range(2, 102):
        candidate = f'{base}-{i}'
        if candidate not in taken:
            return candidate
    raise ValueError(f'slug collision: too many variants of "{base}"')
