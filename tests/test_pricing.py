# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit-Tests für pricing.calc_cost_usd."""
from __future__ import annotations


def test_local_provider_returns_zero():
    from pricing import calc_cost_usd
    assert calc_cost_usd('ollama', 'llama3.1:8b', 1000, 500) == 0.0


def test_local_provider_with_null_tokens_returns_none():
    from pricing import calc_cost_usd
    assert calc_cost_usd('ollama', 'llama3.1:8b', None, None) is None


def test_claude_haiku_pricing():
    from pricing import calc_cost_usd
    # haiku 4.5: 0.80 input / 4.00 output per million tokens
    # 1M input + 1M output -> 0.80 + 4.00 = 4.80
    cost = calc_cost_usd('claude', 'claude-haiku-4-5', 1_000_000, 1_000_000)
    assert cost == 4.80


def test_claude_versioned_model_strips_version():
    from pricing import calc_cost_usd
    cost = calc_cost_usd('claude', 'claude-haiku-4-5-20251001',
                         1_000_000, 1_000_000)
    assert cost == 4.80


def test_openai_gpt_4o_mini_pricing():
    from pricing import calc_cost_usd
    # 0.15 input / 0.60 output per M
    cost = calc_cost_usd('openai', 'gpt-4o-mini', 1_000_000, 1_000_000)
    assert cost == 0.75


def test_unknown_model_returns_none():
    from pricing import calc_cost_usd
    assert calc_cost_usd('claude', 'unknown-model-xyz', 100, 100) is None


def test_custom_provider_returns_none_for_unknown_model():
    """custom-Provider: pauschal als kostenpflichtig behandelt;
    bei unbekanntem Modell -> None."""
    from pricing import calc_cost_usd
    assert calc_cost_usd('custom', 'some-local-model', 100, 100) is None
