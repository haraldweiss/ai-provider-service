# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cline cost calculation + override-file merging.

Cline publishes no static per-token rate card (rates live behind the
auth-walled dashboard at https://app.cline.bot/dashboard/usage), so Cline
costs are supplied via pricing_overrides_cline.json using the
`cline::<provider/model>` key form.
"""
from __future__ import annotations
import json
import pricing
from pricing import calc_cost_usd


def test_cline_unknown_model_returns_none_without_override(tmp_path, monkeypatch):
    # With an empty override and no static snapshot entry, an unknown model
    # returns None (no crash).
    override = tmp_path / 'pricing_overrides_cline.json'
    override.write_text(json.dumps({}))
    monkeypatch.setattr(pricing, '_CLINE_OVERRIDE_PATH', override)
    assert calc_cost_usd('cline', 'anthropic/claude-sonnet-4-6', 100, 100) is None


def test_cline_real_catalog_override_loads_rates():
    # The committed pricing_overrides_cline.json (sourced from Cline's OSS
    # model catalog, USD per 1M tokens) is loaded by default.
    assert calc_cost_usd('cline', 'anthropic/claude-sonnet-4.6', 1_000_000, 1_000_000) == 18.0
    assert calc_cost_usd('cline', 'openai/gpt-4o', 1_000_000, 1_000_000) == 12.5
    # Unknown model still yields None.
    assert calc_cost_usd('cline', 'cline/does-not-exist', 100, 100) is None


def test_cline_override_file_is_merged(tmp_path, monkeypatch):
    """pricing_overrides_cline.json überschreibt/ergänzt den Snapshot."""
    override = tmp_path / 'pricing_overrides_cline.json'
    override.write_text(json.dumps({
        'cline::anthropic/claude-sonnet-4-6': {'in': 3.0, 'out': 15.0},
    }))
    monkeypatch.setattr(pricing, '_CLINE_OVERRIDE_PATH', override)

    cost = calc_cost_usd('cline', 'anthropic/claude-sonnet-4-6', 1_000_000, 1_000_000)
    assert cost == round(3.0 + 15.0, 6)


def test_cline_override_preserves_model_slash(tmp_path, monkeypatch):
    """Cline model IDs keep their own slash (provider/model)."""
    override = tmp_path / 'pricing_overrides_cline.json'
    override.write_text(json.dumps({
        'cline::openai/gpt-4o': {'in': 2.5, 'out': 10.0},
    }))
    monkeypatch.setattr(pricing, '_CLINE_OVERRIDE_PATH', override)

    assert calc_cost_usd('cline', 'openai/gpt-4o', 1_000_000, 1_000_000) == 12.5


def test_cline_override_does_not_clobber_zai(tmp_path, monkeypatch):
    """Der Cline-Override-File berührt z.ai-Preise nicht."""
    override = tmp_path / 'pricing_overrides_cline.json'
    override.write_text(json.dumps({
        'cline::anthropic/claude-sonnet-4-6': {'in': 3.0, 'out': 15.0},
    }))
    monkeypatch.setattr(pricing, '_CLINE_OVERRIDE_PATH', override)

    # z.ai free model bleibt frei (kommt aus dem statischen Snapshot)
    assert calc_cost_usd('zai', 'glm-4.5-flash', 1_000_000, 1_000_000) == 0.0
