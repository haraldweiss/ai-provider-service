# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cost-Berechnung für Provider-Calls.

Statischer Snapshot — manuell pflegen. Bei Bedarf später durch Sync gegen
LiteLLM ersetzen, analog zum Tracker.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from typing import Optional

# USD pro 1M Tokens. Quelle: manuell, Stand Mai 2026.
_PRICING_USD_PER_MTOK: dict[tuple[str, str], dict[str, float]] = {
    ('claude', 'claude-opus-4-7'):    {'in': 15.0, 'out': 75.0},
    ('claude', 'claude-sonnet-4-6'):  {'in':  3.0, 'out': 15.0},
    ('claude', 'claude-haiku-4-5'):   {'in':  0.8, 'out':  4.0},
    ('openai', 'gpt-4o'):             {'in':  2.5, 'out': 10.0},
    ('openai', 'gpt-4o-mini'):        {'in':  0.15, 'out': 0.6},
    # opencode.ai Zen rate card — Stand Mai 2026, USD per 1M tokens.
    # Quelle: https://opencode.ai/docs/zen/#pricing
    ('opencode', 'big-pickle'):               {'in': 0.0, 'out': 0.0},
    ('opencode', 'deepseek-v4-flash-free'):   {'in': 0.0, 'out': 0.0},
    ('opencode', 'mimo-v2.5-free'):           {'in': 0.0, 'out': 0.0},
    ('opencode', 'nemotron-3-super-free'):    {'in': 0.0, 'out': 0.0},
    ('opencode', 'minimax-m2.7'):             {'in': 0.30, 'out': 1.20},
    ('opencode', 'minimax-m2.5'):             {'in': 0.30, 'out': 1.20},
    ('opencode', 'glm-5.1'):                  {'in': 1.40, 'out': 4.40},
    ('opencode', 'glm-5'):                    {'in': 1.00, 'out': 3.20},
    ('opencode', 'kimi-k2.5'):                {'in': 0.60, 'out': 3.00},
    ('opencode', 'kimi-k2.6'):                {'in': 0.95, 'out': 4.00},
    ('opencode', 'qwen3.6-plus'):             {'in': 0.50, 'out': 3.00},
    ('opencode', 'qwen3.5-plus'):             {'in': 0.20, 'out': 1.20},
    ('opencode', 'grok-build-0.1'):           {'in': 1.00, 'out': 2.00},
    ('opencode', 'claude-opus-4.8'):          {'in': 5.00, 'out': 25.00},
    ('opencode', 'claude-opus-4.7'):          {'in': 5.00, 'out': 25.00},
    ('opencode', 'claude-opus-4.6'):          {'in': 5.00, 'out': 25.00},
    ('opencode', 'claude-opus-4.5'):          {'in': 5.00, 'out': 25.00},
    ('opencode', 'claude-opus-4.1'):          {'in': 15.00, 'out': 75.00},
    ('opencode', 'claude-sonnet-4.6'):        {'in': 3.00, 'out': 15.00},
    ('opencode', 'claude-sonnet-4.5'):        {'in': 3.00, 'out': 15.00},
    ('opencode', 'claude-sonnet-4'):          {'in': 3.00, 'out': 15.00},
    ('opencode', 'claude-haiku-4.5'):         {'in': 1.00, 'out': 5.00},
    ('opencode', 'gemini-3.5-flash'):         {'in': 1.50, 'out': 9.00},
    ('opencode', 'gemini-3.1-pro'):           {'in': 2.00, 'out': 12.00},
    ('opencode', 'gemini-3-flash'):           {'in': 0.50, 'out': 3.00},
    ('opencode', 'gpt-5.5'):                  {'in': 5.00, 'out': 30.00},
    ('opencode', 'gpt-5.5-pro'):              {'in': 30.00, 'out': 180.00},
    ('opencode', 'gpt-5.4'):                  {'in': 2.50, 'out': 15.00},
    ('opencode', 'gpt-5.4-pro'):              {'in': 30.00, 'out': 180.00},
    ('opencode', 'gpt-5.4-mini'):             {'in': 0.75, 'out': 4.50},
    ('opencode', 'gpt-5.4-nano'):             {'in': 0.20, 'out': 1.25},
    ('opencode', 'gpt-5.3-codex-spark'):      {'in': 1.75, 'out': 14.00},
    ('opencode', 'gpt-5.3-codex'):            {'in': 1.75, 'out': 14.00},
    ('opencode', 'gpt-5.2'):                  {'in': 1.75, 'out': 14.00},
    ('opencode', 'gpt-5.2-codex'):            {'in': 1.75, 'out': 14.00},
    ('opencode', 'gpt-5.1'):                  {'in': 1.07, 'out': 8.50},
    ('opencode', 'gpt-5.1-codex'):            {'in': 1.07, 'out': 8.50},
    ('opencode', 'gpt-5.1-codex-max'):        {'in': 1.25, 'out': 10.00},
    ('opencode', 'gpt-5.1-codex-mini'):       {'in': 0.25, 'out': 2.00},
    ('opencode', 'gpt-5'):                    {'in': 1.07, 'out': 8.50},
    ('opencode', 'gpt-5-codex'):              {'in': 1.07, 'out': 8.50},
    ('opencode', 'gpt-5-nano'):               {'in': 0.05, 'out': 0.40},
    # z.ai (Zhipu / GLM) rate card — USD per 1M tokens.
    # Quelle: https://docs.z.ai/guides/overview/pricing.md (Stand Juni 2026).
    # Täglich via `flask update-zai-pricing` aktualisiert → pricing_overrides_zai.json.
    ('zai', 'glm-5.1'):                       {'in': 1.4, 'out': 4.4},
    ('zai', 'glm-5'):                         {'in': 1.0, 'out': 3.2},
    ('zai', 'glm-5-turbo'):                   {'in': 1.2, 'out': 4.0},
    ('zai', 'glm-4.7'):                       {'in': 0.6, 'out': 2.2},
    ('zai', 'glm-4.7-flashx'):                {'in': 0.07, 'out': 0.4},
    ('zai', 'glm-4.6'):                       {'in': 0.6, 'out': 2.2},
    ('zai', 'glm-4.5'):                       {'in': 0.6, 'out': 2.2},
    ('zai', 'glm-4.5-x'):                     {'in': 2.2, 'out': 8.9},
    ('zai', 'glm-4.5-air'):                   {'in': 0.2, 'out': 1.1},
    ('zai', 'glm-4.5-airx'):                  {'in': 1.1, 'out': 4.5},
    ('zai', 'glm-4-32b-0414-128k'):           {'in': 0.1, 'out': 0.1},
    ('zai', 'glm-4.7-flash'):                 {'in': 0.0, 'out': 0.0},
    ('zai', 'glm-4.5-flash'):                 {'in': 0.0, 'out': 0.0},
    # Vision models
    ('zai', 'glm-5v-turbo'):                  {'in': 1.2, 'out': 4.0},
    ('zai', 'glm-4.6v'):                      {'in': 0.3, 'out': 0.9},
    ('zai', 'glm-ocr'):                       {'in': 0.03, 'out': 0.03},
    ('zai', 'glm-4.6v-flashx'):               {'in': 0.04, 'out': 0.4},
    ('zai', 'glm-4.5v'):                      {'in': 0.6, 'out': 1.8},
    ('zai', 'glm-4.6v-flash'):                {'in': 0.0, 'out': 0.0},
}

# Provider, die immer als kostenfrei (lokal) gelten.
_LOCAL_PROVIDERS = {'ollama'}

# Pfade zu JSON-Override-Dateien (täglich via Cron aktualisierbar).
# Getrennte Dateien pro Provider, damit der opencode-Daily-Cron die z.ai-Preise
# nicht überschreibt (und umgekehrt).
_PRICING_OVERRIDE_PATH = Path(os.path.dirname(__file__)) / 'pricing_overrides.json'
_ZAI_OVERRIDE_PATH = Path(os.path.dirname(__file__)) / 'pricing_overrides_zai.json'


def _merge_override_file(pricing: dict, path: Path) -> None:
    """Mergt eine `{provider::model: {in,out}}`-Override-Datei in-place."""
    try:
        if path.exists():
            raw = json.loads(path.read_text())
            for key, rates in raw.items():
                provider, model = key.split('::', 1)
                pricing[(provider, model)] = rates
    except Exception:
        pass


def _load_merged_pricing() -> dict[tuple[str, str], dict[str, float]]:
    """Gibt die gemergte Pricing-Dict zurück: statisch + Overrides."""
    pricing = dict(_PRICING_USD_PER_MTOK)
    _merge_override_file(pricing, _PRICING_OVERRIDE_PATH)
    _merge_override_file(pricing, _ZAI_OVERRIDE_PATH)
    return pricing


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
    _pricing = _load_merged_pricing()
    rates = _pricing.get((provider_id, model)) \
        or _pricing.get((provider_id, _strip_version(model)))
    if not rates:
        return None
    return round(
        (input_tokens * rates['in'] + output_tokens * rates['out']) / 1_000_000,
        6,
    )
