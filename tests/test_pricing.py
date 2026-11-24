# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit-Tests für pricing.calc_cost_usd."""
from __future__ import annotations
import pytest


def test_local_provider_returns_zero():
    from pricing import calc_cost_usd
    assert calc_cost_usd('ollama', 'llama3.1:8b', 1000, 500) == 0.0


def test_omlx_is_local_and_has_no_provider_charge():
    from pricing import calc_cost_usd
    assert calc_cost_usd('omlx', 'qwen', 1000, 500) == 0.0


def test_local_provider_with_null_tokens_returns_none():
    from pricing import calc_cost_usd
    assert calc_cost_usd('ollama', 'llama3.1:8b', None, None) is None


def test_claude_haiku_pricing():
    from pricing import calc_cost_usd
    # haiku 4.5: 1.00 input / 5.00 output per million tokens
    # 1M input + 1M output -> 1.00 + 5.00 = 6.00
    cost = calc_cost_usd('claude', 'claude-haiku-4-5', 1_000_000, 1_000_000)
    assert cost == 6.0


def test_claude_versioned_model_strips_version():
    from pricing import calc_cost_usd
    cost = calc_cost_usd('claude', 'claude-haiku-4-5-20251001',
                         1_000_000, 1_000_000)
    assert cost == 6.0


def test_claude_current_model_pricing():
    from pricing import calc_cost_usd
    # Opus 5.5: 4.00 input / 20.00 output per million tokens.
    assert calc_cost_usd('claude', 'claude-opus-5-5', 1_000_000, 1_000_000) == 24.0
    # Haiku 5.5: 0.10 / 0.50 for prompts up to 100k tokens.
    assert calc_cost_usd('claude', 'claude-haiku-5-5', 0, 1_000_000) == 0.5
    # Sonnet 5.5: 2.00 / 10.00.
    assert calc_cost_usd('claude', 'claude-sonnet-5-5', 0, 1_000_000) == 10.0


def test_claude_cache_read_multiplier_is_model_specific():
    from pricing import calc_cost_usd
    # Standard models read cache at 0.1x input: 1M reads on Opus 4.7 (5.00) = 0.50
    assert calc_cost_usd('claude', 'claude-opus-4-7', 0, 0,
                         cache_read_input_tokens=1_000_000) == 0.5
    # Opus 5.5 / Sonnet 5.5 read at 0.05x: 4.00 * 0.05 = 0.20
    assert calc_cost_usd('claude', 'claude-opus-5-5', 0, 0,
                         cache_read_input_tokens=1_000_000) == 0.2
    assert calc_cost_usd('claude', 'claude-sonnet-5-5', 0, 0,
                         cache_read_input_tokens=1_000_000) == 0.1
    # Fable/Mythos 5.2 read at 0.025x: 8.00 * 0.025 = 0.20
    assert calc_cost_usd('claude', 'claude-fable-5-2', 0, 0,
                         cache_read_input_tokens=1_000_000) == 0.2


def test_claude_haiku_5_5_long_prompt_tier():
    from pricing import calc_cost_usd
    # Under the 100k threshold: base rate 0.10/M input.
    assert calc_cost_usd('claude', 'claude-haiku-5-5', 100_000, 0) == 0.01
    # Over the threshold the whole request is billed at 5x (0.50/M input).
    assert calc_cost_usd('claude', 'claude-haiku-5-5', 100_001, 0) == pytest.approx(0.05, rel=0.01)
    # Cache reads count toward the prompt length for the threshold:
    # 50k input + 50,001 cache reads = 100,001 prompt tokens.
    assert calc_cost_usd('claude', 'claude-haiku-5-5', 50_000, 0,
                         cache_read_input_tokens=50_001) == pytest.approx(0.0275, rel=0.01)
    # Other models keep standard pricing on 1M-token prompts.
    assert calc_cost_usd('claude', 'claude-opus-5-5', 1_000_000, 0) == 4.0


def test_openai_gpt_4o_mini_pricing():
    from pricing import calc_cost_usd
    # 0.15 input / 0.60 output per M
    cost = calc_cost_usd('openai', 'gpt-4o-mini', 1_000_000, 1_000_000)
    assert cost == 0.75


def test_openai_reported_model_version_keeps_alias_rate():
    from pricing import calc_cost_usd
    assert calc_cost_usd('openai', 'gpt-4o-2024-08-06', 1_000_000, 1_000_000) == 12.5


def test_unknown_model_returns_none():
    from pricing import calc_cost_usd
    assert calc_cost_usd('claude', 'unknown-model-xyz', 100, 100) is None


def test_custom_provider_returns_none_for_unknown_model():
    """custom-Provider: pauschal als kostenpflichtig behandelt;
    bei unbekanntem Modell -> None."""
    from pricing import calc_cost_usd
    assert calc_cost_usd('custom', 'some-local-model', 100, 100) is None


def test_opencode_gpt5_pricing():
    from pricing import calc_cost_usd
    cost = calc_cost_usd('opencode', 'gpt-5', 1_000_000, 1_000_000)
    assert cost == pytest.approx(9.57, rel=0.01)


def test_opencode_claude_sonnet_pricing():
    from pricing import calc_cost_usd
    cost = calc_cost_usd('opencode', 'claude-sonnet-4.6', 1_000_000, 1_000_000)
    assert cost == pytest.approx(18.0, rel=0.01)


def test_opencode_free_model():
    from pricing import calc_cost_usd
    cost = calc_cost_usd('opencode', 'deepseek-v4-flash-free', 1000, 500)
    assert cost == 0.0


def test_opencode_pricing_none_for_unknown_model():
    from pricing import calc_cost_usd
    cost = calc_cost_usd('opencode', 'definitely-not-a-real-model', 100, 100)
    assert cost is None
