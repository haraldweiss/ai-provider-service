"""Tests for the async /v1/models cache (model_cache.py + wiring)."""

import time

import model_cache
import health_tracker
from config import Config


def test_put_get_roundtrip():
    rows = [{'id': 'ollama/x'}]
    model_cache.put('u1', rows)
    assert model_cache.get('u1') == rows


def test_get_missing_returns_none():
    assert model_cache.get('nobody') is None


def test_expired_entry_returns_none():
    model_cache.put('u1', [{'id': 'a'}])
    assert model_cache.get('u1') is not None
    # Simulate expiry without sleeping.
    model_cache._cache['u1']['expires'] = time.time() - 1
    assert model_cache.get('u1') is None


def test_invalidate_single_user():
    model_cache.put('u1', [{'id': 'a'}])
    model_cache.put('u2', [{'id': 'b'}])
    model_cache.invalidate('u1')
    assert model_cache.get('u1') is None
    assert model_cache.get('u2') is not None


def test_invalidate_all():
    model_cache.put('u1', [{'id': 'a'}])
    model_cache.put('u2', [{'id': 'b'}])
    model_cache.invalidate()
    assert model_cache.get('u1') is None
    assert model_cache.get('u2') is None
    assert model_cache.cached_user_ids() == []


def test_ttl_config_applied(monkeypatch):
    monkeypatch.setattr(Config, 'MODEL_CACHE_TTL_SEC', 7)
    model_cache.put('u1', [{'id': 'a'}])
    assert 6 < model_cache._cache['u1']['expires'] - time.time() <= 7


def test_available_model_rows_serves_from_cache(monkeypatch):
    import api.openai_api as openai_api

    calls = []

    def fake_build(uid):
        calls.append(uid)
        return [{'id': 'ollama/a'}]

    monkeypatch.setattr(openai_api, '_build_model_rows', fake_build)
    rows1 = openai_api._available_model_rows('u2')
    rows2 = openai_api._available_model_rows('u2')
    assert rows1 == rows2 == [{'id': 'ollama/a'}]
    assert calls == ['u2'], 'second call must come from cache'


def test_invalidate_forces_rebuild(monkeypatch):
    import api.openai_api as openai_api

    calls = []
    monkeypatch.setattr(
        openai_api, '_build_model_rows',
        lambda uid: calls.append(uid) or [{'id': 'x'}])
    openai_api._available_model_rows('u3')
    model_cache.invalidate('u3')
    openai_api._available_model_rows('u3')
    assert calls == ['u3', 'u3']


def test_health_transition_invalidates_cache():
    # Fake provider: first set (None -> True) is not a transition.
    health_tracker.set_status('fakeprov', True)
    model_cache.put('u4', [{'id': 'y'}])
    assert model_cache.get('u4') is not None

    # True -> False is a transition and must drop all cached rows.
    health_tracker.set_status('fakeprov', False, reason='boom')
    assert model_cache.get('u4') is None

    # Restore neutral state for other tests.
    health_tracker._status.pop('fakeprov', None)


def test_worker_refresh_rebuilds_cached_users(app, monkeypatch):
    import api.openai_api as openai_api
    import worker

    model_cache.put('u5', [{'id': 'stale'}])
    monkeypatch.setattr(
        openai_api, '_build_model_rows', lambda uid: [{'id': 'fresh'}])
    worker._refresh_model_cache(app)
    assert model_cache.get('u5') == [{'id': 'fresh'}]


def test_worker_refresh_skips_unknown_users(app, monkeypatch):
    import api.openai_api as openai_api
    import worker

    def boom(uid):
        raise AssertionError('must not build for users not in cache')

    monkeypatch.setattr(openai_api, '_build_model_rows', boom)
    worker._refresh_model_cache(app)  # cache empty -> no-op, must not raise
