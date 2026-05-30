# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for Config additions: admin/gate/opencode env vars."""

import importlib
import os

import pytest

# Pre-import api.auth so its `Config` reference binds to the original
# Config class. Without this, when these tests run before any other
# test that triggers app creation, api.auth would later import the
# *reloaded* Config — leaving conftest's `Config.SERVICE_TOKEN` mutation
# stranded on the old class object (breaks auth in subsequent suites).
import api.auth  # noqa: F401


@pytest.fixture(autouse=True)
def _restore_config():
    """Ensure config module is reloaded back to env-default state after each
    test in this module, so module-level state changes do not leak."""
    yield
    import config
    importlib.reload(config)


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
