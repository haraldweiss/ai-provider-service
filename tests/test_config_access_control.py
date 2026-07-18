# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Config additions: admin/gate/opencode env vars."""

import importlib


def reload_config():
    import config
    importlib.reload(config)
    return config.Config


def test_admin_user_id_defaults_to_harald(monkeypatch):
    monkeypatch.delenv('ADMIN_USER_ID', raising=False)
    Config = reload_config()
    assert Config.ADMIN_USER_ID == 'harald'


def test_ungated_providers_default(monkeypatch):
    monkeypatch.delenv('UNGATED_PROVIDERS', raising=False)
    Config = reload_config()
    assert Config.UNGATED_PROVIDERS == {'ollama'}


def test_ungated_providers_env_override(monkeypatch):
    monkeypatch.setenv('UNGATED_PROVIDERS', 'ollama,custom')
    Config = reload_config()
    assert Config.UNGATED_PROVIDERS == {'ollama', 'custom'}


def test_ungated_providers_env_override_cannot_include_colibri(monkeypatch):
    monkeypatch.setenv('UNGATED_PROVIDERS', 'ollama,colibri,custom')
    Config = reload_config()

    assert Config.UNGATED_PROVIDERS == {'ollama', 'custom'}
    assert 'colibri' not in Config.UNGATED_PROVIDERS


def test_gate_enabled_default_false(monkeypatch):
    monkeypatch.delenv('GATE_ENABLED', raising=False)
    Config = reload_config()
    assert Config.GATE_ENABLED is False


def test_gate_enabled_truthy(monkeypatch):
    monkeypatch.setenv('GATE_ENABLED', 'true')
    Config = reload_config()
    assert Config.GATE_ENABLED is True


def test_opencode_base_url_default(monkeypatch):
    monkeypatch.delenv('OPENCODE_BASE_URL', raising=False)
    Config = reload_config()
    assert Config.OPENCODE_BASE_URL == 'https://opencode.ai/zen/v1'


def test_colibri_config_defaults(monkeypatch):
    for name in (
        'COLIBRI_ENABLED', 'COLIBRI_BASE_URL', 'COLIBRI_API_KEY',
        'COLIBRI_MODEL_ID', 'COLIBRI_CONNECT_TIMEOUT_SEC',
        'COLIBRI_STREAM_READ_TIMEOUT_SEC', 'COLIBRI_MAX_WAITING',
        'COLIBRI_QUEUE_TIMEOUT_SEC',
    ):
        monkeypatch.delenv(name, raising=False)

    Config = reload_config()

    assert Config.COLIBRI_ENABLED is False
    assert Config.COLIBRI_BASE_URL == 'http://host.docker.internal:18000'
    assert Config.COLIBRI_API_KEY == ''
    assert Config.COLIBRI_MODEL_ID == 'glm-5.2-colibri'
    assert Config.COLIBRI_CONNECT_TIMEOUT_SEC == 10
    assert Config.COLIBRI_STREAM_READ_TIMEOUT_SEC == 180
    assert Config.COLIBRI_MAX_WAITING == 1
    assert Config.COLIBRI_QUEUE_TIMEOUT_SEC == 60
