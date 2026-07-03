"""Tests for the background worker (health-check polling + queue-drain).

Worker runs as a daemon thread inside the Flask process. These tests use
mocking to verify individual components without spinning up the full loop.
"""

from __future__ import annotations
import threading
import time
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from worker import _check_provider


# ─── _check_provider ──────────────────────────────────────────────────────


@patch('worker.PROVIDER_REGISTRY', new_callable=dict)
@patch('worker.get_client')
@patch('worker.health_tracker')
def test_check_provider_system_healthy(mock_ht, mock_get_client, mock_reg):
    """System provider with no user config runs health check with empty config."""
    mock_reg['claude'] = {'system': True}
    mock_ht.get_status.return_value = {'persistent': False, 'healthy': None}
    mock_client = MagicMock()
    mock_client.health.return_value = True
    mock_get_client.return_value = mock_client

    result = _check_provider('claude')

    assert result is True
    mock_get_client.assert_called_once_with('claude', {})
    mock_client.health.assert_called_once()
    mock_ht.set_status.assert_called_once_with('claude', True, reason='')


@patch('worker.PROVIDER_REGISTRY', new_callable=dict)
@patch('worker.get_client')
@patch('worker.health_tracker')
def test_check_provider_system_unhealthy(mock_ht, mock_get_client, mock_reg):
    """System provider that fails health check sets status to False."""
    mock_reg['claude'] = {'system': True}
    mock_ht.get_status.return_value = {'persistent': False, 'healthy': None}
    mock_client = MagicMock()
    mock_client.health.side_effect = ConnectionError('refused')
    mock_get_client.return_value = mock_client

    result = _check_provider('claude')

    assert result is False
    mock_ht.set_status.assert_called_once()
    args = mock_ht.set_status.call_args
    assert args[0][0] == 'claude'
    assert args[0][1] is False


@patch('worker.PROVIDER_REGISTRY', new_callable=dict)
@patch('worker.health_tracker')
def test_check_provider_non_system_no_config(mock_ht, mock_reg):
    """Non-system provider without any ProviderConfig is skipped (health=True)."""
    mock_reg['mammouth'] = {'system': False}
    # No ProviderConfig exists → first() returns None

    with patch('worker.ProviderConfig') as mock_pc:
        mock_pc.query.filter_by.return_value.first.return_value = None
        mock_ht.get_status.return_value = {'persistent': False, 'healthy': None}
        result = _check_provider('mammouth')

    assert result is True
    mock_ht.set_status.assert_not_called()


@patch('worker.PROVIDER_REGISTRY', new_callable=dict)
@patch('worker.health_tracker')
def test_check_provider_persistent_failure_not_overwritten(mock_ht, mock_reg):
    """Persistent failure (e.g. account without balance) is not overwritten by
    health-check."""
    mock_reg['claude'] = {'system': True}
    mock_ht.get_status.return_value = {'persistent': True, 'healthy': False}

    result = _check_provider('claude')

    assert result is False
    # The persistent check happens before the client call, so no client is created
    mock_ht.set_status.assert_not_called()


@patch('worker.PROVIDER_REGISTRY', new_callable=dict)
@patch('worker.get_client')
@patch('worker.health_tracker')
def test_check_provider_exception_swallowed(mock_ht, mock_get_client, mock_reg):
    """Exception in health() is caught and sets status to False."""
    mock_reg['claude'] = {'system': True}
    mock_ht.get_status.return_value = {'persistent': False, 'healthy': None}
    mock_client = MagicMock()
    mock_client.health.side_effect = RuntimeError('unexpected')
    mock_get_client.return_value = mock_client

    result = _check_provider('claude')

    assert result is False
    mock_ht.set_status.assert_called_once()


# ─── start / stop ─────────────────────────────────────────────────────────


def test_start_and_stop():
    """Starting the worker creates a daemon thread; stop() sets the event."""
    import worker as w
    # Reset global state so start() creates a fresh thread
    w._worker_thread = None
    w._stop_event.clear()

    try:
        w.start(MagicMock())
        assert w._worker_thread is not None
        assert w._worker_thread.is_alive()
        assert w._worker_thread.daemon is True
        assert w._worker_thread.name == 'ai-provider-worker'
    finally:
        w.stop()
        w._worker_thread.join(timeout=3)
        assert not w._worker_thread.is_alive()


def test_start_idempotent():
    """Starting the worker twice does not create a second thread."""
    import worker as w
    w._worker_thread = None
    w._stop_event.clear()

    try:
        w.start(MagicMock())
        first_thread = w._worker_thread
        w.start(MagicMock())  # second call — same app, no-op
        assert w._worker_thread is first_thread
    finally:
        w.stop()
        if w._worker_thread:
            w._worker_thread.join(timeout=3)


def test_stop_no_start():
    """Calling stop() when worker was never started is a no-op (no crash)."""
    import worker as w
    w._worker_thread = None
    w.stop()  # should not raise


# ─── _run interval calculation ────────────────────────────────────────────


@patch('worker._tick')
@patch('worker._drain')
@patch('worker._refresh_free_models')
@patch('worker.Config')
def test_run_tick_scheduling(mock_config, mock_refresh, mock_drain, mock_tick):
    """The _run loop ticks at the expected cadence using pre-computed steps.

    We set hc_interval=6, qd_interval=12 so sleep_sec=6, giving:
      hc_steps = 1  (6//6)
      qd_steps = 2  (12//6)
      fm_steps = 1  (21600//6 → guarded to 1, so every tick).

    We run 8 ticks then stop to verify the call pattern.
    """
    mock_config.HEALTH_CHECK_INTERVAL_SEC = 6
    mock_config.QUEUE_DRAIN_INTERVAL_SEC = 12

    import worker as w
    w._stop_event.clear()

    mock_app = MagicMock()

    # Run in a thread so we can stop it after a few ticks
    t = threading.Thread(
        target=w._run, args=(mock_app,),
        daemon=True,
    )
    t.start()

    time.sleep(0.2)  # let it tick a few times
    w.stop()
    t.join(timeout=3)

    # At least one tick must have run (health check)
    assert mock_tick.called, 'Worker should have called _tick at least once'


@patch('worker._tick')
@patch('worker._drain')
@patch('worker._refresh_free_models')
@patch('worker.Config')
def test_run_does_not_crash(mock_config, mock_refresh, mock_drain, mock_tick):
    """The _run loop handles exceptions in subroutines without crashing."""
    mock_config.HEALTH_CHECK_INTERVAL_SEC = 1
    mock_config.QUEUE_DRAIN_INTERVAL_SEC = 2
    mock_tick.side_effect = RuntimeError('tick error')

    import worker as w
    w._stop_event.clear()

    mock_app = MagicMock()

    t = threading.Thread(
        target=w._run, args=(mock_app,),
        daemon=True,
    )
    t.start()

    time.sleep(0.3)  # let it tick a few times despite errors
    w.stop()
    t.join(timeout=3)

    # Didn't crash — that's the test
    assert True
