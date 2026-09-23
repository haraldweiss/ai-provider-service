# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for /configs persistence — fallback_provider / fallback_model.

Regression guard: `save_config` used to accept `fallback_provider` but silently
ignore `fallback_model`, so a configured fallback always reused the requested
model name (and failed on providers that don't host it).
"""

HEADERS = {'Authorization': 'Bearer test-token'}


def _post(client, body):
    return client.post('/configs/harald/openrouter', json=body, headers=HEADERS)


def _get(client):
    return client.get('/configs/harald/openrouter', headers=HEADERS)


def test_save_config_persists_fallback_model(client):
    r = _post(client, {
        'config': {},
        'fallback_provider': 'ollama',
        'fallback_model': 'ollama/oracle-llama3.2:3b',
    })
    assert r.status_code == 200
    assert r.get_json()['fallback_provider'] == 'ollama'
    assert r.get_json()['fallback_model'] == 'ollama/oracle-llama3.2:3b'

    got = _get(client).get_json()
    assert got['fallback_provider'] == 'ollama'
    assert got['fallback_model'] == 'ollama/oracle-llama3.2:3b'


def test_fallback_model_empty_string_becomes_none(client):
    r = _post(client, {
        'config': {},
        'fallback_provider': 'ollama',
        'fallback_model': '',
    })
    assert r.status_code == 200
    assert r.get_json()['fallback_model'] is None
    assert _get(client).get_json()['fallback_model'] is None


def test_fallback_model_omitted_preserves_existing(client):
    _post(client, {'config': {}, 'fallback_model': 'ollama/oracle-llama3.2:3b'})

    # A later save that only touches the provider must not wipe the model.
    _post(client, {'config': {}, 'fallback_provider': 'ollama'})
    assert _get(client).get_json()['fallback_model'] == 'ollama/oracle-llama3.2:3b'
