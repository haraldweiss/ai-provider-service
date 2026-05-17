# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cost-Berechnung für Provider-Calls.

Statischer Snapshot — manuell pflegen. Bei Bedarf später durch Sync gegen
LiteLLM ersetzen, analog zum Tracker.
"""
from __future__ import annotations
import re
from typing import Optional

# USD pro 1M Tokens. Quelle: manuell, Stand Mai 2026.
_PRICING_USD_PER_MTOK: dict[tuple[str, str], dict[str, float]] = {
    ('claude', 'claude-opus-4-7'):    {'in': 15.0, 'out': 75.0},
    ('claude', 'claude-sonnet-4-6'):  {'in':  3.0, 'out': 15.0},
    ('claude', 'claude-haiku-4-5'):   {'in':  0.8, 'out':  4.0},
    ('openai', 'gpt-4o'):             {'in':  2.5, 'out': 10.0},
    ('openai', 'gpt-4o-mini'):        {'in':  0.15, 'out': 0.6},
}

# Provider, die immer als kostenfrei (lokal) gelten.
_LOCAL_PROVIDERS = {'ollama'}


def _strip_version(model: str) -> str:
    """Entfernt das Anthropic-Date-Suffix.
    'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'."""
    return re.sub(r'-\d{8}$', '', model)


def calc_cost_usd(
    provider_id: str, model: str,
    input_tokens: Optional[int], output_tokens: Optional[int],
) -> Optional[float]:
    """USD-Kosten für einen Call. None bei unbekanntem Modell oder
    fehlenden Token-Counts."""
    if input_tokens is None or output_tokens is None:
        return None
    if provider_id in _LOCAL_PROVIDERS:
        return 0.0
    # 'custom' Provider: pauschal als kostenpflichtig. Bei unbekanntem Modell
    # None — Sub-Projekt B kann das Endpoint-aware machen (lokale LM-Studio
    # Endpoints als kostenfrei erkennen).
    rates = _PRICING_USD_PER_MTOK.get((provider_id, model)) \
        or _PRICING_USD_PER_MTOK.get((provider_id, _strip_version(model)))
    if not rates:
        return None
    return round(
        (input_tokens * rates['in'] + output_tokens * rates['out']) / 1_000_000,
        6,
    )
