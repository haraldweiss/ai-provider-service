# SPDX-License-Identifier: AGPL-3.0-or-later
"""z.ai (GLM) cost calculation + override-file merging."""
from __future__ import annotations
import json
import pricing
from pricing import calc_cost_usd


def test_zai_paid_model_cost():
    # glm-4.6: $0.6 in / $2.2 out per 1M tokens
    cost = calc_cost_usd('zai', 'glm-4.6', 1_000_000, 1_000_000)
    assert cost == round(0.6 + 2.2, 6)


def test_zai_free_model_is_zero():
    assert calc_cost_usd('zai', 'glm-4.5-flash', 1_000_000, 1_000_000) == 0.0


def test_zai_unknown_model_returns_none():
    assert calc_cost_usd('zai', 'glm-does-not-exist', 100, 100) is None


def test_zai_override_file_is_merged(tmp_path, monkeypatch):
    """pricing_overrides_zai.json überschreibt/ergänzt den statischen Snapshot."""
    override = tmp_path / 'pricing_overrides_zai.json'
    override.write_text(json.dumps({'zai::glm-future': {'in': 9.0, 'out': 18.0}}))
    monkeypatch.setattr(pricing, '_ZAI_OVERRIDE_PATH', override)

    cost = calc_cost_usd('zai', 'glm-future', 1_000_000, 1_000_000)
    assert cost == round(9.0 + 18.0, 6)


def test_zai_override_does_not_clobber_opencode(tmp_path, monkeypatch):
    """Der z.ai-Override-File berührt opencode-Preise nicht."""
    override = tmp_path / 'pricing_overrides_zai.json'
    override.write_text(json.dumps({'zai::glm-4.6': {'in': 1.0, 'out': 1.0}}))
    monkeypatch.setattr(pricing, '_ZAI_OVERRIDE_PATH', override)

    # opencode free model bleibt frei (kommt aus dem statischen Snapshot)
    assert calc_cost_usd('opencode', 'deepseek-v4-flash-free', 1_000_000, 1_000_000) == 0.0
