# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cline OSS model-catalog sync: TS parser, ClinePass dedup, price diff.

`flask update-cline-catalog` fetches Cline's OSS catalog.generated.ts, diffs it
against the committed pricing_overrides_cline.json and reports for review. This
suite tests the pure functions (no network) that back the report + --apply.
"""
from __future__ import annotations
import json
import cli

# Representative snippet of catalog.generated.ts: unquoted top-level keys,
# strict JSON below, nested ModelInfo pricing.
SAMPLE_TS = r"""
export const GENERATED_PROVIDER_MODELS: {
  version: number
  providers: Record<string, Record<string, ModelInfo>>
} = {
  version: 1786732960562,
  providers: {
    "zai": {
      "glm-5.2": {
        "id": "glm-5.2",
        "name": "GLM-5.2",
        "contextWindow": 200000,
        "pricing": { "input": 1.4, "output": 4.4, "cacheRead": 0.26 }
      },
      "glm-4.7-flash": {
        "id": "glm-4.7-flash",
        "pricing": { "input": 0.07, "output": 0.4 }
      }
    },
    "deepseek": {
      "deepseek-v4-pro": {
        "id": "deepseek-v4-pro",
        "pricing": { "input": 0.3, "output": 1.0 }
      }
    },
    "ollama": {
      "llama3": {
        "id": "llama3",
        "name": "Llama 3",
        "pricing": {}
      }
    }
  }
};
"""


def _override_for_test():
    return {
        '_meta': {
            'clinepass_models': ['cline-pass/glm-5.2', 'cline-pass/deepseek-v4-pro'],
            'total_models': 5,
        },
        # paid non-pass duplicate of cline-pass/glm-5.2 -> should drop
        'cline::zai/glm-5.2': {'in': 1.4, 'out': 4.4},
        # paid variant, gets its own cline-pass/ version -> should drop
        'cline::deepseek/deepseek-v4-pro': {'in': 0.5, 'out': 2.0},
        # covered by pass, but free -> user wants it KEPT
        'cline::zai/glm-4.7-flash:free': {'in': 0.0, 'out': 0.0},
        # not covered by any cline-pass model -> stays
        'cline::openai/gpt-4o': {'in': 2.5, 'out': 10.0},
        # the pass variant itself stays
        'cline::cline-pass/glm-5.2': {'in': 0.0, 'out': 0.0},
        'cline::cline-pass/deepseek-v4-pro': {'in': 0.0, 'out': 0.0},
    }


def test_extract_catalog_providers_parses_ts():
    providers = cli._extract_catalog_providers(SAMPLE_TS)
    assert set(providers) == {'zai', 'deepseek', 'ollama'}
    assert 'glm-5.2' in providers['zai']
    assert providers['zai']['glm-5.2']['pricing']['input'] == 1.4


def test_catalog_pricing_flattens_and_skips_unpriced():
    providers = cli._extract_catalog_providers(SAMPLE_TS)
    pricing = cli._catalog_pricing(providers)
    # ollama/llama3 has empty pricing -> skipped
    assert set(pricing) == {
        'zai/glm-5.2', 'zai/glm-4.7-flash', 'deepseek/deepseek-v4-pro'}
    assert pricing['zai/glm-5.2'] == (1.4, 4.4)


def test_clinepass_drop_candidates_drops_paid_non_pass_keeps_free():
    dropped = cli._clinepass_drop_candidates(_override_for_test())
    # paid non-pass duplicates of covered models drop; :free and pass variants keep
    assert dropped == ['cline::deepseek/deepseek-v4-pro', 'cline::zai/glm-5.2']


def test_clinepass_drop_keeps_free_covered_variant():
    # glm-4.7-flash:free is a free covered variant -> not dropped even though its
    # tail matches nothing in clinepass list here... use a covered tail.
    ov = {
        '_meta': {'clinepass_models': ['cline-pass/glm-5.2']},
        'cline::zai/glm-5.2:free': {'in': 0.0, 'out': 0.0},
        'cline::cline-pass/glm-5.2': {'in': 0.0, 'out': 0.0},
    }
    assert cli._clinepass_drop_candidates(ov) == []


def test_clinepass_drop_is_case_insensitive_on_tail():
    ov = {
        '_meta': {'clinepass_models': ['cline-pass/Qwen3.7-Plus']},
        'cline::alibaba/qwen3.7-plus': {'in': 0.5, 'out': 3.0},   # drop
        'cline::cline-pass/Qwen3.7-Plus': {'in': 0.0, 'out': 0.0},
    }
    assert cli._clinepass_drop_candidates(ov) == ['cline::alibaba/qwen3.7-plus']


def test_price_changes_reports_only_exact_matches():
    catalog = {'zai/glm-5.2': (1.5, 5.0), 'openai/gpt-4o': (3.0, 15.0)}
    changes = cli._price_changes(_override_for_test(), catalog)
    # zai/glm-5.2 matches with new price; openai/gpt-4o matches with new price;
    # deepseek/deepseek-v4-pro not in catalog; pass/free entries not exact-matched
    assert 'cline::zai/glm-5.2' in changes
    assert changes['cline::zai/glm-5.2'] == (
        {'in': 1.4, 'out': 4.4}, {'in': 1.5, 'out': 5.0})
    assert 'cline::openai/gpt-4o' in changes


def test_apply_clinepass_and_prices_writes_curated_file():
    override = _override_for_test()
    catalog = {'zai/glm-5.2': (1.5, 5.0)}
    new_file, dropped, changes = cli.apply_clinepass_and_prices(
        json.loads(json.dumps(override)), catalog)
    assert dropped == ['cline::deepseek/deepseek-v4-pro', 'cline::zai/glm-5.2']
    # dropped key stays gone even though it had a catalog price match
    assert 'cline::zai/glm-5.2' not in new_file
    assert 'cline::deepseek/deepseek-v4-pro' not in new_file
    # pass + free variants retained, gpt-4o retained
    model_keys = {k for k in new_file if k.startswith('cline::')}
    assert 'cline::cline-pass/glm-5.2' in model_keys
    assert 'cline::zai/glm-4.7-flash:free' in model_keys
    assert 'cline::openai/gpt-4o' in model_keys


def test_refresh_cline_meta_updates_counts_and_apply_note():
    data = json.loads(json.dumps(_override_for_test()))
    cli._refresh_cline_meta(data, ['cline::zai/glm-5.2'])
    # non-_meta keys after drop (we simulate the pre-drop dict here)
    model_keys = [k for k in data if k.startswith('cline::')]
    assert data['_meta']['total_models'] == len(model_keys)
    assert data['_meta']['clinepass_count'] == 2
    assert data['_meta']['last_apply']['at']
    assert data['_meta']['last_apply']['dropped_pass_dups'] == [
        'cline::zai/glm-5.2']


# --- daily change-notification snapshot/diff ---------------------------------

def test_save_and_load_cline_snapshot(tmp_path, monkeypatch):
    target = tmp_path / 'pricing_overrides_cline.json.snapshot'
    monkeypatch.setattr(cli, 'CLINE_SNAPSHOT_PATH', target)

    served = {'cline::openai/gpt-4o': {'in': 2.5, 'out': 10.0},
              '_meta': {'total_models': 1}}
    save_cline_snapshot = cli.save_cline_snapshot(served)

    assert save_cline_snapshot == target
    loaded = cli.load_cline_snapshot()
    assert loaded['served'] == {'cline::openai/gpt-4o': {'in': 2.5, 'out': 10.0}}


def test_snapshot_rotation_writes_prev(tmp_path, monkeypatch):
    target = tmp_path / 'pricing_overrides_cline.json.snapshot'
    monkeypatch.setattr(cli, 'CLINE_SNAPSHOT_PATH', target)

    cli.save_cline_snapshot({'cline::a/x': {'in': 1.0, 'out': 1.0}})
    cli.save_cline_snapshot({'cline::b/y': {'in': 2.0, 'out': 2.0}})

    assert target.exists()
    prev = target.with_suffix(target.suffix + '.prev')
    assert prev.exists()
    # .prev holds the previous (rotated) snapshot
    assert json.loads(prev.read_text())['served'] == {
        'cline::a/x': {'in': 1.0, 'out': 1.0}}
    # current holds the newest
    assert json.loads(target.read_text())['served'] == {
        'cline::b/y': {'in': 2.0, 'out': 2.0}}


def test_diff_cline_served_detects_add_remove_change():
    old = {'served': {'cline::a/x': {'in': 1.0, 'out': 1.0},
                      'cline::b/y': {'in': 2.0, 'out': 2.0},
                      'cline::c/z': {'in': 3.0, 'out': 3.0}}}
    new = {'served': {'cline::b/y': {'in': 2.5, 'out': 3.0},   # changed
                      'cline::c/z': {'in': 3.0, 'out': 3.0},   # same
                      'cline::d/w': {'in': 4.0, 'out': 4.0}}}  # added
    diff = cli.diff_cline_served(old, new)
    assert set(diff['added']) == {'cline::d/w'}
    assert set(diff['removed']) == {'cline::a/x'}
    assert set(diff['changed']) == {'cline::b/y'}


def test_format_cline_change_email_lists_all_sections():
    diff = {
        'added': {'cline::d/w': {'in': 4.0, 'out': 4.0}},
        'removed': {'cline::a/x': {'in': 1.0, 'out': 1.0}},
        'changed': {'cline::b/y': ({'in': 2.0, 'out': 2.0},
                                   {'in': 2.5, 'out': 3.0})},
    }
    body = cli._format_cline_change_email(diff)
    assert '+ d/w' in body
    assert '- a/x' in body
    assert '~ b/y' in body

