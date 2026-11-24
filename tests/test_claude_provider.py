# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit-Tests für den Claude-Client (Models-API-Discovery + Fallback)."""
from __future__ import annotations
from types import SimpleNamespace

import pytest

import providers.claude as claude_mod
from providers.claude import ClaudeClient, DEFAULT_MODEL, KNOWN_MODELS


class FakeModels:
    def __init__(self, models=None, error=None):
        self.models = list(models or [])
        self.error = error
        self.calls: list[dict] = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return [SimpleNamespace(id=model) for model in self.models]


def make_client(models=None, error=None, api_key='sk-ant-test'):
    client = object.__new__(ClaudeClient)
    client._api_key = api_key
    client.client = SimpleNamespace(models=FakeModels(models, error))
    return client


@pytest.fixture(autouse=True)
def _reset_models_cache():
    claude_mod._models_cache.update({'key': None, 'ts': 0.0, 'models': []})
    yield


def test_get_models_uses_models_api():
    client = make_client(models=['claude-opus-5-5', 'claude-haiku-5-5'])
    assert client.get_models() == ['claude-haiku-5-5', 'claude-opus-5-5']
    # No lifecycle filter: the API default is "callable by this key"
    # (active + deprecated, never retired).
    assert client.client.models.calls == [{}]


def test_get_models_falls_back_to_static_list_on_api_error():
    client = make_client(error=RuntimeError('401 unauthorized'))
    assert client.get_models() == KNOWN_MODELS


def test_get_models_falls_back_when_api_returns_nothing():
    client = make_client(models=[])
    assert client.get_models() == KNOWN_MODELS


def test_get_models_env_override_wins_without_api_call(monkeypatch):
    monkeypatch.setattr(claude_mod.Config, 'CLAUDE_MODEL_LIST',
                        'claude-haiku-5-5, claude-opus-5-5')
    client = make_client(models=['claude-sonnet-5-5'])
    assert client.get_models() == ['claude-haiku-5-5', 'claude-opus-5-5']
    assert client.client.models.calls == []


def test_get_models_caches_api_result():
    client = make_client(models=['claude-haiku-5-5'])
    client.get_models()
    client.get_models()
    assert len(client.client.models.calls) == 1


def test_get_models_cache_is_scoped_per_api_key():
    first = make_client(models=['claude-haiku-5-5'], api_key='key-a')
    second = make_client(models=['claude-opus-5-5'], api_key='key-b')
    first.get_models()
    assert second.get_models() == ['claude-opus-5-5']
    assert len(first.client.models.calls) == 1
    assert len(second.client.models.calls) == 1


def test_static_model_list_is_current_and_contains_default():
    assert DEFAULT_MODEL in KNOWN_MODELS
    # Retired Claude API IDs must not be offered as usable models.
    assert 'claude-opus-4-1-20250805' not in KNOWN_MODELS
    assert 'claude-sonnet-4-5' in KNOWN_MODELS
    assert 'claude-opus-5-5' in KNOWN_MODELS


def test_default_model_is_used_when_request_has_no_model():
    sent = {}

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type='text', text='OK')],
                stop_reason='end_turn',
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

    client = make_client()
    client.client.messages = FakeMessages()
    result = client.create_message('', [{'role': 'user', 'content': 'hi'}])
    assert sent['model'] == DEFAULT_MODEL
    assert result['content'][0]['text'] == 'OK'
