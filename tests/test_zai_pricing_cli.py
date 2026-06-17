# SPDX-License-Identifier: AGPL-3.0-or-later
"""z.ai pricing sync: markdown parser, diff, save, change notification.

Daily job (`flask update-zai-pricing`) fetches the z.ai pricing page (clean
markdown at docs.z.ai/.../pricing.md), parses the rate card, diffs against the
last snapshot, persists it and emails the owner on any tariff change.
"""
from __future__ import annotations
import json
import cli

# Repräsentativer Ausschnitt der echten docs.z.ai/.../pricing.md Tabellen.
SAMPLE_MD = r"""
# Pricing

## Models

### Text Models

Prices per 1M tokens.

| Model               | Input  | Cached Input | Cached Input Storage | Output |
| :------------------ | :----- | :----------- | :------------------- | :----- |
| GLM-4.6             | \$0.6  | \$0.11       | Limited-time Free    | \$2.2  |
| GLM-4.5-Air         | \$0.2  | \$0.03       | Limited-time Free    | \$1.1  |
| GLM-4-32B-0414-128K | \$0.1  | -            | -                    | \$0.1  |
| GLM-4.5-Flash       | Free   | Free         | Free                 | Free   |

### Vision Models

Prices per 1M tokens.

| Model      | Input  | Cached Input | Cached Input Storage | Output |
| :--------- | :----- | :----------- | :------------------- | :----- |
| GLM-4.6V   | \$0.3  | \$0.05       | Limited-time Free    | \$0.9  |

### Built-in Tools

| Tool       | Cost         |
| :--------- | :----------- |
| Web Search | \$0.01 / use |
"""


def test_parse_zai_pricing_extracts_text_and_vision_models():
    data = cli._parse_zai_pricing(SAMPLE_MD)
    assert data['zai::glm-4.6'] == {'in': 0.6, 'out': 2.2}
    assert data['zai::glm-4.5-air'] == {'in': 0.2, 'out': 1.1}
    assert data['zai::glm-4-32b-0414-128k'] == {'in': 0.1, 'out': 0.1}
    assert data['zai::glm-4.6v'] == {'in': 0.3, 'out': 0.9}


def test_parse_zai_pricing_free_models_are_zero():
    data = cli._parse_zai_pricing(SAMPLE_MD)
    assert data['zai::glm-4.5-flash'] == {'in': 0.0, 'out': 0.0}


def test_parse_zai_pricing_ignores_non_token_tables():
    data = cli._parse_zai_pricing(SAMPLE_MD)
    # Built-in Tools table has no Input/Output columns → must not appear
    assert not any('web' in k.lower() for k in data)


def test_parse_zai_pricing_uses_lowercased_api_ids():
    data = cli._parse_zai_pricing(SAMPLE_MD)
    assert all(k.startswith('zai::') and k == k.lower() for k in data)


def test_diff_pricing_detects_add_remove_change():
    old = {'zai::glm-4.6': {'in': 0.6, 'out': 2.2},
           'zai::glm-old': {'in': 1.0, 'out': 1.0}}
    new = {'zai::glm-4.6': {'in': 0.5, 'out': 2.0},
           'zai::glm-new': {'in': 0.2, 'out': 0.4}}
    diff = cli._diff_pricing(old, new)
    assert 'zai::glm-new' in diff['added']
    assert 'zai::glm-old' in diff['removed']
    assert 'zai::glm-4.6' in diff['changed']
    assert diff['changed']['zai::glm-4.6'] == ({'in': 0.6, 'out': 2.2},
                                               {'in': 0.5, 'out': 2.0})


def test_diff_pricing_empty_when_identical():
    same = {'zai::glm-4.6': {'in': 0.6, 'out': 2.2}}
    diff = cli._diff_pricing(same, dict(same))
    assert not diff['added'] and not diff['removed'] and not diff['changed']


def test_save_zai_pricing_writes_override_file(tmp_path, monkeypatch):
    import pricing
    target = tmp_path / 'pricing_overrides_zai.json'
    monkeypatch.setattr(pricing, '_ZAI_OVERRIDE_PATH', target)
    data = {'zai::glm-4.6': {'in': 0.6, 'out': 2.2}}

    path = cli.save_zai_pricing(data)

    assert path == target
    assert json.loads(target.read_text()) == data
