"""In-memory sliding-window rate limiter tests."""

import time
import pytest
from unittest.mock import patch, MagicMock
from api.ratelimit import _check, _key, _RATE_LIMITS, _windows


@pytest.fixture(autouse=True)
def clear_windows():
    _windows.clear()


def test_under_limit_allows():
    bucket = 'memory:write'
    limit, window = _RATE_LIMITS[bucket]
    for _ in range(limit - 1):
        assert _check(bucket, limit, window) is True


def test_at_limit_allows():
    bucket = 'memory:write'
    limit, window = _RATE_LIMITS[bucket]
    for _ in range(limit):
        assert _check(bucket, limit, window) is True


def test_over_limit_blocks():
    bucket = 'memory:write'
    limit, window = _RATE_LIMITS[bucket]
    for _ in range(limit):
        _check(bucket, limit, window)
    assert _check(bucket, limit, window) is False


def test_window_expires():
    bucket = 'vault:export'
    limit = 5
    window = 60
    for _ in range(limit):
        assert _check(bucket, limit, window) is True
    assert _check(bucket, limit, window) is False
    with patch('api.ratelimit.time.time', return_value=time.time() + 61):
        assert _check(bucket, limit, window) is True


def test_different_buckets_independent():
    write_limit, write_window = _RATE_LIMITS['memory:write']
    read_limit, read_window = _RATE_LIMITS['memory:read']
    for _ in range(write_limit):
        _check('memory:write', write_limit, write_window)
    assert _check('memory:read', read_limit, read_window) is True
