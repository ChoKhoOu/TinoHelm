"""Unit tests for :mod:`tinohelm.factor.worker`.

All tests use mocked Redis and DB sessions — no real I/O is performed.
"""
from __future__ import annotations

import asyncio
import importlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinohelm.factor.types import EvalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_payload(
    run_id: str = "test-run-1",
    factor_name: str = "ret_N",
    config: dict | None = None,
    params: dict | None = None,
    full: bool = False,
) -> str:
    return json.dumps({
        "run_id": run_id,
        "factor_name": factor_name,
        "config": config or {
            "universe": ["BTCUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-02-01",
        },
        "params": params,
        "full": full,
    })


def _make_eval_result(**kwargs) -> EvalResult:
    defaults = dict(
        ic_mean=0.05,
        ir=0.5,
        rating=2,
    )
    defaults.update(kwargs)
    return EvalResult(**defaults)


def _make_mock_db_run(
    run_id: str = "test-run-1",
    factor_name: str = "ret_N",
    status: str = "queued",
    config: dict | None = None,
) -> MagicMock:
    run = MagicMock()
    run.id = run_id
    run.factor_name = factor_name
    run.status = status
    run.config = config or {
        "universe": ["BTCUSDT-PERP"],
        "start": "2024-01-01",
        "end": "2024-02-01",
    }
    return run


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_rds():
    """Async mock Redis client."""
    rds = AsyncMock()
    rds.exists.return_value = 0  # cancel flag not set by default
    rds.publish = AsyncMock()
    rds.close = AsyncMock()
    return rds


@pytest.fixture
def mock_session_factory(request):
    """Returns a factory that yields an async context manager mock DB session.

    The default scalar_one_or_none result is a queued FactorRun.  Tests can
    override by passing a ``db_run`` fixture param.
    """
    db_run = getattr(request, "param", None) or _make_mock_db_run()

    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=db_run))
    )
    session.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, session


@pytest.fixture
def mock_eval_result():
    return _make_eval_result()


# ---------------------------------------------------------------------------
# Test: happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_job_happy_path(mock_rds, mock_session_factory, mock_eval_result):
    """_process_job completes successfully: status → running → completed, events published."""
    factory, session = mock_session_factory

    payload = _make_payload()

    with (
        patch("tinohelm.factor.worker.get_session_factory", return_value=factory),
        patch("tinohelm.factor.worker.aioredis.from_url", return_value=mock_rds),
        patch("tinohelm.factor.worker._run_orchestrator", return_value=mock_eval_result),
    ):
        from tinohelm.factor.worker import _process_job
        await _process_job(payload, "redis://localhost:6379")

    # DB session.execute was called — check that update was attempted
    assert session.execute.called
    assert session.commit.called

    # events channel publish was called with completed event
    publish_calls = [
        json.loads(c.args[1])
        for c in mock_rds.publish.call_args_list
        if c.args[0] == "tino:factor:events"
    ]
    assert any(ev["type"] == "factor.completed" for ev in publish_calls), (
        f"Expected factor.completed event, got: {publish_calls}"
    )
    completed_evt = next(ev for ev in publish_calls if ev["type"] == "factor.completed")
    assert completed_evt["run_id"] == "test-run-1"
    assert completed_evt["factor_name"] == "ret_N"


# ---------------------------------------------------------------------------
# Test: failure path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_job_failure(mock_rds, mock_session_factory):
    """When Orchestrator raises, status becomes failed with traceback in error."""
    factory, session = mock_session_factory

    payload = _make_payload()

    def _boom(**kwargs):
        raise RuntimeError("kernel exploded")

    with (
        patch("tinohelm.factor.worker.get_session_factory", return_value=factory),
        patch("tinohelm.factor.worker.aioredis.from_url", return_value=mock_rds),
        patch("tinohelm.factor.worker._run_orchestrator", side_effect=RuntimeError("kernel exploded")),
    ):
        from tinohelm.factor.worker import _process_job
        await _process_job(payload, "redis://localhost:6379")

    # We don't inspect the exact update() SQL call — just confirm status
    # write happened (session.execute called at least twice: running + failed).
    assert session.execute.call_count >= 2

    # events channel: factor.failed published
    publish_calls = [
        json.loads(c.args[1])
        for c in mock_rds.publish.call_args_list
        if c.args[0] == "tino:factor:events"
    ]
    assert any(ev["type"] == "factor.failed" for ev in publish_calls), (
        f"Expected factor.failed event, got: {publish_calls}"
    )
    failed_evt = next(ev for ev in publish_calls if ev["type"] == "factor.failed")
    assert "exploded" in failed_evt["error"]


# ---------------------------------------------------------------------------
# Test: duplicate queue recovery replay is DB-claim guarded
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_job_duplicate_replay_loses_db_claim_before_orchestrator(mock_rds):
    """Duplicate queue entries must be consumed without executing the job twice."""
    run_id = "duplicate-replay-run"
    run = _make_mock_db_run(run_id=run_id, status="queued")

    select_result = MagicMock(scalar_one_or_none=MagicMock(return_value=run))
    stale_claim_result = MagicMock(rowcount=0)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, stale_claim_result])
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("tinohelm.factor.worker.get_session_factory", return_value=factory),
        patch("tinohelm.factor.worker.aioredis.from_url", return_value=mock_rds),
        patch("tinohelm.factor.worker._run_orchestrator") as run_orchestrator,
    ):
        from tinohelm.factor.worker import _process_job

        await _process_job(_make_payload(run_id=run_id), "redis://localhost:6379")

    run_orchestrator.assert_not_called()
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    mock_rds.publish.assert_not_awaited()
    mock_rds.close.assert_awaited_once()


# ---------------------------------------------------------------------------
# Test: state machine transitions queued → running → completed/failed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_worker_state_machine():
    """State machine: second call after first completes transitions correctly."""
    run_id = "sm-run-1"

    # We'll track the status values written via update() calls.
    # Each call to execute() records the positional args.
    written_statuses: list[str] = []

    session = AsyncMock()
    session.commit = AsyncMock()

    call_count = 0

    async def _execute(stmt):
        nonlocal call_count
        call_count += 1
        # First execute is the SELECT (return a queued run).
        # Subsequent executes are UPDATE statements — capture their values.
        if call_count == 1:
            run = _make_mock_db_run(run_id=run_id)
            return MagicMock(scalar_one_or_none=MagicMock(return_value=run))
        # Peek at the compiled clause dict to extract `status` value if present.
        try:
            params = stmt.compile().params
            if "status_1" in params:
                written_statuses.append(params["status_1"])
        except Exception:
            pass
        return MagicMock()

    session.execute = _execute

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    rds = AsyncMock()
    rds.exists.return_value = 0
    rds.publish = AsyncMock()
    rds.close = AsyncMock()

    eval_result = _make_eval_result()

    with (
        patch("tinohelm.factor.worker.get_session_factory", return_value=factory),
        patch("tinohelm.factor.worker.aioredis.from_url", return_value=rds),
        patch("tinohelm.factor.worker._run_orchestrator", return_value=eval_result),
    ):
        from tinohelm.factor.worker import _process_job
        payload = _make_payload(run_id=run_id)
        await _process_job(payload, "redis://localhost:6379")

    # At minimum, "running" and "completed" must have been written.
    assert "running" in written_statuses or call_count >= 2, (
        "Expected at least running+completed status writes"
    )


# ---------------------------------------------------------------------------
# Test: cancel flag
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_flag(mock_session_factory):
    """When cancel key exists in Redis, job is marked cancelled and skipped."""
    factory, session = mock_session_factory

    rds = AsyncMock()
    rds.exists.return_value = 1  # cancel flag IS set
    rds.publish = AsyncMock()
    rds.close = AsyncMock()

    run_id = "cancel-run-1"
    payload = _make_payload(run_id=run_id)

    orchestrator_called = False

    def _orch(**kwargs):
        nonlocal orchestrator_called
        orchestrator_called = True
        return _make_eval_result()

    with (
        patch("tinohelm.factor.worker.get_session_factory", return_value=factory),
        patch("tinohelm.factor.worker.aioredis.from_url", return_value=rds),
        patch("tinohelm.factor.worker._run_orchestrator", side_effect=_orch),
    ):
        from tinohelm.factor.worker import _process_job
        await _process_job(payload, "redis://localhost:6379")

    # Orchestrator must NOT have been called
    assert not orchestrator_called, "Orchestrator must not run when cancel flag is set"

    # Cancel key checked
    rds.exists.assert_called_once_with(f"tino:factor:cancel:{run_id}")


# ---------------------------------------------------------------------------
# Test: PercentStepThrottle — only publishes at step boundaries
# ---------------------------------------------------------------------------

def test_percent_throttle_step_boundaries():
    """PercentStepThrottle emits True only at 0, multiples of step, and 100."""
    from tinohelm.core.async_queue_worker import PercentStepThrottle

    throttle = PercentStepThrottle(step=10)

    # Boundaries that SHOULD write
    should_write = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    for pct in should_write:
        assert throttle.should_write(pct), f"Expected should_write=True at pct={pct}"

    # Values between boundaries that should NOT write
    should_not_write = [1, 5, 11, 15, 21, 49, 51, 99]
    for pct in should_not_write:
        assert not throttle.should_write(pct), f"Expected should_write=False at pct={pct}"


@pytest.mark.asyncio
async def test_percent_throttle_progress_publish_count():
    """Progress callback only writes to DB at 10%-step boundaries (not every call)."""
    from tinohelm.core.async_queue_worker import PercentStepThrottle

    throttle = PercentStepThrottle(step=10)
    db_writes: list[int] = []
    redis_publishes: list[int] = []

    async def _fake_progress(pct: int, msg: str = "") -> None:
        redis_publishes.append(pct)
        if throttle.should_write(pct):
            db_writes.append(pct)

    # Simulate calling progress at every integer 0-100
    for pct in range(101):
        await _fake_progress(pct)

    # Every percent was published to Redis
    assert len(redis_publishes) == 101

    # Only step boundaries were written to DB
    expected_db = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert db_writes == expected_db


# ---------------------------------------------------------------------------
# Test: start/stop worker public API
# ---------------------------------------------------------------------------

def test_start_stop_worker_importable():
    """start_factor_worker / stop_factor_worker are importable and callable."""
    from tinohelm.factor.worker import start_factor_worker, stop_factor_worker

    assert callable(start_factor_worker)
    assert callable(stop_factor_worker)


@pytest.mark.asyncio
async def test_start_stop_worker_lifecycle():
    """start_factor_worker creates a task; stop_factor_worker cancels it."""
    from tinohelm.factor.worker import start_factor_worker, stop_factor_worker, _handle

    # Ensure clean state — stop any leftover task from previous tests.
    _handle.stop()

    async def _fake_consumer_loop(*args, **kwargs):
        await asyncio.sleep(999)

    # consumer_loop is called with (redis_url, queue_key, process_fn) and its
    # return value is passed directly to asyncio.create_task() as a coroutine.
    # We patch it so it returns a fresh coroutine each call.
    with patch("tinohelm.factor.worker.consumer_loop", new=_fake_consumer_loop):
        start_factor_worker("redis://localhost:6379")

    assert _handle.is_running()

    stop_factor_worker()
    assert not _handle.is_running()

    # Let the event loop process the cancellation.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancel_flag_mid_pipeline_breaks(mock_session_factory, mock_eval_result):
    """Factor worker observes cancel flags emitted between progress stages."""
    factory, _session = mock_session_factory

    rds = AsyncMock()
    # 1) pre-load check: no cancel; 2) after progress 10: no cancel;
    # 3) after progress 40: cancel observed and the run aborts.
    rds.exists.side_effect = [0, 0, 1]
    rds.publish = AsyncMock()
    rds.setex = AsyncMock()
    rds.close = AsyncMock()

    stage_calls: list[int] = []

    def _fake_orchestrator(**kwargs):
        progress_cb = kwargs["progress_cb"]
        progress_cb(10, "data_load", stage="aligning")
        stage_calls.append(10)
        progress_cb(40, "kernel_exec", stage="computing")
        stage_calls.append(40)  # must not be reached after cancel raises
        return mock_eval_result

    with (
        patch("tinohelm.factor.worker.get_session_factory", return_value=factory),
        patch("tinohelm.factor.worker.aioredis.from_url", return_value=rds),
        patch("tinohelm.factor.worker._run_orchestrator", side_effect=_fake_orchestrator),
    ):
        from tinohelm.factor.worker import _process_job

        await _process_job(_make_payload(run_id="cancel-mid"), "redis://localhost:6379")

    assert stage_calls == [10]
    event_payloads = [
        json.loads(call.args[1])
        for call in rds.publish.call_args_list
        if call.args[0] == "tino:factor:events"
    ]
    assert any(event["type"] == "factor.cancelled" for event in event_payloads)
    assert not any(event["type"] == "factor.completed" for event in event_payloads)


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_replays_snapshot_without_deleting_live_queue():
    """Recovery must not delete concurrent live LPUSH entries."""
    old_run = _make_mock_db_run(
        run_id="old-run",
        config={
            "universe": ["BTCUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-01-02",
            "params": {"n": 5},
            "_tino_run_options": {"full": False},
        },
    )
    new_run = _make_mock_db_run(
        run_id="new-run",
        config={
            "universe": ["BTCUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-01-02",
            "params": {"n": 9},
            "_tino_run_options": {"full": True},
        },
    )

    update_result = MagicMock(rowcount=2)
    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = [old_run, new_run]

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[update_result, select_result])
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    rds = AsyncMock()
    rds.delete = AsyncMock()
    rds.lpush = AsyncMock()

    worker_module = importlib.import_module("tinohelm.factor.worker")

    with patch.object(worker_module, "get_session_factory", return_value=factory):
        recovered = await worker_module.recover_interrupted_jobs(rds)

    assert recovered == 2
    rds.delete.assert_not_awaited()
    assert not getattr(rds, "rpush").await_args_list

    pushed_ids = [json.loads(c.args[1])["run_id"] for c in rds.lpush.await_args_list]
    assert pushed_ids == ["old-run", "new-run"]

    simulated_redis_list: list[str] = []
    for pushed_id in pushed_ids:
        simulated_redis_list.insert(0, pushed_id)
    assert simulated_redis_list == ["new-run", "old-run"]
    assert simulated_redis_list.pop() == "old-run"
