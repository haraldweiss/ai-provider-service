"""Shared request-validation helpers for API blueprints."""

from __future__ import annotations


def parse_max_tokens(value, default: int) -> int:
    """Parse the optional ``max_tokens`` request field.

    ``None`` (absent or explicit JSON null) returns ``default``. A present
    but invalid value (wrong type, non-numeric string, or < 1) raises
    ``ValueError`` — callers map that to HTTP 400.
    """
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError('max_tokens must be an integer') from None
    if parsed < 1:
        raise ValueError('max_tokens must be a positive integer')
    return parsed
