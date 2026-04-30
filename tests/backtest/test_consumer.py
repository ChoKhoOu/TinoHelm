"""Unit tests for tinohelm.backtest.consumer — asyncio consumer pool."""
from __future__ import annotations

import asyncio
import json
import signal
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rds(*, brpop_result=None, get_result=None):
    """Create an async mock Redis client."""
    rds = AsyncMock()
    rds.brpop = AsyncMock(return_value=brpop_result)
    rds.get = AsyncMock(return_value=get_result)
    rds.delete = AsyncMock()
    rds.lpush = AsyncMock()
    return rds


def _make_proc(*, returncode=None):
    """Create a fake asyncio subprocess mock."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdin = AsyncMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdin.close = MagicMock()
    proc.stdin.wait_closed = AsyncMock()
    proc.wait = AsyncMock(return_value=0)
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Test 1: start/stop lifecycle
# ---------------------------------------------------------------------------

async def test_start_stop_lifecycle():
    """start_consumers spawns n tasks; stop_consumers cancels them all."""
    # Import fresh each time to avoid _shutdown_event state bleed
    import tinohelm.backtest.consumer as consumer_mod

    rds = _make_rds()
    # BRPOP returns None (timeout) indefinitely so consumers idle without doing work
    rds.brpop = AsyncMock(return_value=None)

    with patch("tinohelm.backtest.consumer.aioredis.from_url", return_value=rds):
        tasks, returned_rds = await consumer_mod.start_consumers(
            n=2,
            redis_url="redis://localhost:6379",
            catalog_path="/tmp/catalog",
            artifacts_path="/tmp/artifacts",
            db_url="sqlite:///:memory:",
        )

    assert len(tasks) == 2
    assert returned_rds is rds, "start_consumers must return the redis connection as second element"
    assert all(not t.done() for t in tasks), "Tasks should still be running after start"

    await consumer_mod.stop_consumers(tasks, rds, timeout=3.0)

    # Allow the event loop to process cancellations
    await asyncio.sleep(0)

    assert all(t.done() for t in tasks), "All tasks must be done after stop_consumers"


# ---------------------------------------------------------------------------
# Test 2: cancel watcher sends SIGTERM
# ---------------------------------------------------------------------------

async def test_cancel_watcher_sends_sigterm():
    """_cancel_watcher sends SIGTERM when cancel flag is set in Redis."""
    from tinohelm.backtest.consumer import _cancel_watcher

    proc = _make_proc(returncode=None)
    # proc.wait returns 0 — SIGTERM causes the watcher to exit the while loop
    # because _cancel_watcher calls proc.wait() inside wait_for, which resolves.
    proc.wait = AsyncMock(return_value=0)

    rds = _make_rds(get_result=b"1")  # cancel flag is set

    await _cancel_watcher(
        "r-1",
        proc,
        rds,
        poll_interval=0.01,
        sigterm_grace=1.0,
    )

    proc.send_signal.assert_called_once_with(signal.SIGTERM)
    rds.delete.assert_called_once_with("tino:backtest:cancel:r-1")


# ---------------------------------------------------------------------------
# Test 3: cancel watcher sends SIGKILL after SIGTERM timeout
# ---------------------------------------------------------------------------

async def test_cancel_watcher_sigkill_on_timeout():
    """_cancel_watcher sends SIGKILL when SIGTERM grace period expires."""
    from tinohelm.backtest.consumer import _cancel_watcher

    proc = _make_proc(returncode=None)

    # First call to proc.wait() hangs (SIGTERM timeout path).
    # Second call (after proc.kill()) returns immediately.
    call_count = 0

    async def _wait_side_effect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Hang indefinitely — simulates process ignoring SIGTERM
            await asyncio.sleep(9999)
        else:
            # After kill(), return immediately
            return -9

    proc.wait = _wait_side_effect

    rds = _make_rds()  # cancel flag is set immediately

    async def _get(key: str):
        if key == "tino:backtest:cancel:r-timeout":
            return b"1"
        return None

    rds.get.side_effect = _get

    # Use short grace to speed up test; sigterm_grace=0.1s
    await _cancel_watcher(
        "r-timeout",
        proc,
        rds,
        poll_interval=0.01,
        sigterm_grace=0.1,
    )

    proc.send_signal.assert_called_once_with(signal.SIGTERM)
    proc.kill.assert_called_once()


async def test_cancel_watcher_does_not_sigkill_terminalizing_subprocess():
    """Late cancel must not SIGKILL a child that finishes inside terminalization grace."""
    from tinohelm.backtest.consumer import _cancel_watcher

    proc = _make_proc(returncode=None)
    first_wait_cancelled = False

    async def _wait_side_effect():
        nonlocal first_wait_cancelled
        if not first_wait_cancelled:
            first_wait_cancelled = True
            await asyncio.sleep(9999)
        return 0

    proc.wait = _wait_side_effect
    rds = _make_rds()

    async def _get(key: str):
        if key == "tino:backtest:cancel:r-terminalizing":
            return b"1"
        if key == "tino:backtest:terminalizing:r-terminalizing":
            return b"1"
        return None

    rds.get.side_effect = _get

    await _cancel_watcher(
        "r-terminalizing",
        proc,
        rds,
        poll_interval=0.01,
        sigterm_grace=0.01,
        terminalizing_grace=0.1,
    )

    proc.kill.assert_not_called()


async def test_cancel_watcher_sigkills_stuck_terminalizing_subprocess():
    """Terminalizing marker is a bounded grace, not an infinite worker-slot leak."""
    from tinohelm.backtest.consumer import _cancel_watcher

    proc = _make_proc(returncode=None)
    wait_calls = 0

    async def _wait_side_effect():
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls <= 2:
            await asyncio.sleep(9999)
        return -9

    proc.wait = _wait_side_effect
    rds = _make_rds()

    async def _get(key: str):
        if key == "tino:backtest:cancel:r-stuck-terminalizing":
            return b"1"
        if key == "tino:backtest:terminalizing:r-stuck-terminalizing":
            return b"1"
        return None

    rds.get.side_effect = _get

    await _cancel_watcher(
        "r-stuck-terminalizing",
        proc,
        rds,
        poll_interval=0.01,
        sigterm_grace=0.01,
        terminalizing_grace=0.01,
    )

    proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: shutdown propagates SIGTERM to inflight subprocess
# ---------------------------------------------------------------------------

async def test_shutdown_sigterm_inflight():
    """Cancelling _consumer_loop while a subprocess is running sends SIGTERM."""
    import tinohelm.backtest.consumer as consumer_mod

    # Reset shutdown event
    consumer_mod._shutdown_event.clear()

    proc = _make_proc(returncode=None)

    # proc.wait will hang until cancelled — simulates inflight run
    wait_started = asyncio.Event()
    wait_done_future: asyncio.Future = asyncio.get_event_loop().create_future()

    async def _wait():
        wait_started.set()
        await wait_done_future

    proc.wait = _wait
    # After SIGTERM we expect wait() to be called again (proc.wait() in the except block)
    # but this second call should resolve immediately (proc.returncode was set)
    # Track send_signal calls
    sigterm_sent = asyncio.Event()
    original_send = proc.send_signal

    def _track_send(sig):
        original_send(sig)
        # After SIGTERM, resolve the wait_done_future so process appears done
        proc.returncode = 0
        if not wait_done_future.done():
            wait_done_future.set_result(0)
        sigterm_sent.set()

    proc.send_signal = MagicMock(side_effect=_track_send)

    rds = _make_rds(brpop_result=("tino:backtest:queue", json.dumps({"run_id": "r-inflight"})))

    with (
        patch("tinohelm.backtest.consumer.aioredis.from_url", return_value=rds),
        patch(
            "tinohelm.backtest.consumer.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        # _cancel_watcher should not fire cancel (cancel key not set)
        patch(
            "tinohelm.backtest.consumer._cancel_watcher",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
    ):
        task = asyncio.create_task(
            consumer_mod._consumer_loop(
                0, rds, "/tmp/catalog", "/tmp/artifacts", "sqlite:///:memory:"
            )
        )

        # Wait for the subprocess wait() to start (consumer is mid-run)
        await asyncio.wait_for(wait_started.wait(), timeout=2.0)

        # Cancel the consumer task — this triggers CancelledError in _consumer_loop
        task.cancel()

        # Wait for SIGTERM to be sent to proc
        try:
            await asyncio.wait_for(sigterm_sent.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass

        # Wait for task to complete
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    proc.send_signal.assert_called_with(signal.SIGTERM)


# ---------------------------------------------------------------------------
# Test 5: recover_interrupted_runs
# ---------------------------------------------------------------------------

async def test_recover_interrupted_runs():
    """recover_interrupted_runs re-queues runs with payload; fails those without."""
    from tinohelm.backtest.consumer import recover_interrupted_runs
    from tinohelm.db.models import RunStatus

    # Build two mock BacktestRun rows
    run_with_payload = MagicMock()
    run_with_payload.job_payload_json = {"run_id": "r-1", "strategy_path": "/s.py"}
    run_with_payload.status = RunStatus.running
    run_with_payload.error = None
    run_with_payload.completed_at = None

    run_without_payload = MagicMock()
    run_without_payload.job_payload_json = None
    run_without_payload.status = RunStatus.running
    run_without_payload.error = None
    run_without_payload.completed_at = None

    # Mock DB session
    session = AsyncMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = [run_with_payload, run_without_payload]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result
    session.execute = AsyncMock(return_value=execute_result)
    session.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    rds = _make_rds()

    with patch(
        "tinohelm.db.session.get_session_factory",
        return_value=factory,
    ):
        result = await recover_interrupted_runs(rds)

    # Should recover exactly 1 run (the one with payload)
    assert result == 1

    # run_with_payload should be reset to queued
    assert run_with_payload.status == RunStatus.queued
    assert run_with_payload.error is None
    assert run_with_payload.completed_at is None

    # run_without_payload should be marked failed
    assert run_without_payload.status == RunStatus.failed
    assert run_without_payload.error == "Interrupted by server restart (no stored payload)"

    # Redis lpush should be called exactly once (for run_with_payload)
    rds.lpush.assert_called_once()
    call_args = rds.lpush.call_args
    assert call_args[0][0] == "tino:backtest:queue"
    pushed_payload = json.loads(call_args[0][1])
    assert pushed_payload["run_id"] == "r-1"

    # DB commit should be called
    session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Test 6: stop_consumers closes the redis connection
# ---------------------------------------------------------------------------

async def test_stop_consumers_closes_redis_connection():
    """stop_consumers must call rds.aclose() exactly once, even with no tasks."""
    import tinohelm.backtest.consumer as consumer_mod

    rds = AsyncMock()
    rds.aclose = AsyncMock()

    # No tasks — stop immediately, but rds.aclose must still be called
    consumer_mod._shutdown_event.clear()
    await consumer_mod.stop_consumers([], rds, timeout=3.0)

    rds.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests 7-9: _fallback_db_status — three-branch coverage
# ---------------------------------------------------------------------------

async def test_fallback_db_status_sigkill_writes_failed():
    """returncode=-9 (SIGKILL/OOM) triggers update_db_status with status='failed'.

    Validates:
    - update_db_status is called exactly once
    - status='failed'
    - error_msg contains the returncode string
    - only_if_not_terminal=True is forwarded (atomic CAS guard)
    - rds.delete is NOT called (only the cancelled branch deletes the key)
    """
    import tinohelm.backtest.consumer as consumer_mod
    from tinohelm.backtest.consumer import _fallback_db_status

    captured: list[tuple] = []

    def _mock_update(db_url, run_id, status, result_summary=None, error_msg=None, *, only_if_not_terminal=False):
        captured.append((db_url, run_id, status, result_summary, error_msg, only_if_not_terminal))

    rds = _make_rds()

    with patch.object(consumer_mod, "update_db_status", _mock_update):
        await _fallback_db_status("run-sigkill", -9, "sqlite:///:memory:", rds)

    assert len(captured) == 1, "update_db_status must be called exactly once"
    db_url, run_id, status, result_summary, error_msg, only_if_not_terminal = captured[0]
    assert status == "failed", f"Expected status='failed', got {status!r}"
    assert error_msg is not None and "-9" in error_msg, (
        f"error_msg must contain the returncode '-9', got {error_msg!r}"
    )
    assert only_if_not_terminal is True, (
        "only_if_not_terminal must be True to prevent overwriting subprocess-written terminal state"
    )
    rds.delete.assert_not_awaited()


async def test_fallback_db_status_returncode2_writes_cancelled():
    """returncode=2 (SIGTERM / cancelled path) writes 'cancelled' and cleans up the cancel key.

    Validates:
    - update_db_status called once with status='cancelled'
    - only_if_not_terminal=True
    - rds.delete is awaited with the correct cancel key
    - error_msg is None (cancelled is user-initiated, no error message)
    """
    import tinohelm.backtest.consumer as consumer_mod
    from tinohelm.backtest.consumer import _fallback_db_status

    captured: list[tuple] = []

    def _mock_update(db_url, run_id, status, result_summary=None, error_msg=None, *, only_if_not_terminal=False):
        captured.append((db_url, run_id, status, result_summary, error_msg, only_if_not_terminal))

    run_id = "run-cancelled"
    rds = _make_rds()

    with patch.object(consumer_mod, "update_db_status", _mock_update):
        await _fallback_db_status(run_id, 2, "sqlite:///:memory:", rds)

    assert len(captured) == 1, "update_db_status must be called exactly once"
    db_url, seen_run_id, status, result_summary, error_msg, only_if_not_terminal = captured[0]
    assert status == "cancelled", f"Expected status='cancelled', got {status!r}"
    assert error_msg is None, f"Cancelled path must not set error_msg, got {error_msg!r}"
    assert only_if_not_terminal is True

    cancel_key = f"tino:backtest:cancel:{run_id}"
    rds.delete.assert_awaited_once_with(cancel_key)


async def test_fallback_db_status_returncode0_is_noop():
    """returncode=0 (normal completion) must be a complete no-op.

    The subprocess is responsible for writing its own terminal status.
    The fallback must not call update_db_status or rds.delete.
    """
    import tinohelm.backtest.consumer as consumer_mod
    from tinohelm.backtest.consumer import _fallback_db_status

    update_called = []

    def _mock_update(*args, **kwargs):
        update_called.append((args, kwargs))

    rds = _make_rds()

    with patch.object(consumer_mod, "update_db_status", _mock_update):
        await _fallback_db_status("run-normal", 0, "sqlite:///:memory:", rds)

    assert len(update_called) == 0, (
        "update_db_status must NOT be called when returncode=0"
    )
    rds.delete.assert_not_awaited()
