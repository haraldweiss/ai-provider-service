"""Content sanitization for LLM-bound text — prevent prompt injection."""

from __future__ import annotations
import re

_MAX_CHARS = 5000


def sanitize_for_summary(text: str, max_chars: int = _MAX_CHARS) -> str:
    """Strip control chars, truncate, escape injection patterns.

    Applied to user-provided text before it enters an LLM prompt.
    """
    if not text:
        return ''
    # Remove ASCII control chars except newline/tab
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    # Escape mustache/squirrel-brace — common injection marker
    cleaned = cleaned.replace('{{', '{\u200b{').replace('}}', '}\u200b}')
    # Escape triple-backtick fence — prevents premature close of code block
    cleaned = cleaned.replace('```', '`\u200b``')
    # Truncate
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + '\n\n[...truncated]'
    return cleaned
