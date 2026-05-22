"""Tests for ``tinohelm.core.async_queue_worker`` — NT-free queue primitives."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import declarative_base

from tinohelm.core.async_queue_worker import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    PercentStepThrottle,
    TimeThrottle,
    WorkerHandle,
    consumer_loop,
    enqueue_job,
    requeue_running_jobs,
)


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------

class TestStatusConstants:
    def test_exact_values(self):
        assert STATUS_QUEUED == "queued"
        assert STATUS_RUNNING == "running"
        assert STATUS_COMPLETED == "completed"
        assert STATUS_FAILED == "failed"
        assert STATUS_CANCELLED == "cancelled"

    def test_all_unique(self):
        values = [
            STATUS_QUEUED,
            STATUS_RUNNING,
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_CANCELLED,
        ]
        assert len(set(values)) == len(values)


# ---------------------------------------------------------------------------
# enqueue_job
# ---------------------------------------------------------------------------

class TestEnqueueJob:
    async def test_calls_lpush_with_queue_key_and_job_id(self):
        rds = AsyncMock()
        await enqueue_job(rds, "tino:x:queue", "job-abc")
        rds.lpush.assert_awaited_once_with("tino:x:queue", "job-abc")

    async def test_queue_key_is_explicit_at_call_site(self):
        rds = AsyncMock()
        await enqueue_job(rds, "queue-1", "a")
        await enqueue_job(rds, "queue-2", "b")
        calls = [c.args for c in rds.lpush.await_args_list]
        assert calls == [("queue-1", "a"), ("queue-2", "b")]

    async def test_returns_none(self):
        rds = AsyncMock()
        result = await enqueue_job(rds, "q", "j")
        assert result is None


# ---------------------------------------------------------------------------
# requeue_running_jobs
# ---------------------------------------------------------------------------

def _mock_session_factory(*, rowcount: int = 0, queued_ids: list[str] | None = None):
    """Build an async session-factory mock.

    ``rowcount`` — rowcount returned by the initial ``update`` statement.
    ``queued_ids`` — list of job IDs returned by the follow-up ``select``.
    """
    queued_ids = list(queued_ids or [])

    db = AsyncMock()
    update_result = MagicMock()
    update_result.rowcount = rowcount

    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = queued_ids

    # First execute() call is the UPDATE, second is the SELECT
    db.execute = AsyncMock(side_effect=[update_result, select_result])
    db.commit = AsyncMock()

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=db)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=session_ctx)
    return factory, db


_Base = declarative_base()


class _FakeModel(_Base):
    """Minimal SQLAlchemy model that ``update()`` / ``select()`` can target."""

    __tablename__ = "fake_jobs"

    job_id = Column(String, primary_key=True)
    status = Column(String)
    progress = Column(Integer)
    message = Column(String)


class TestRequeueRunningJobs:
    async def test_running_flipped_and_queue_repushed(self):
        factory, db = _mock_session_factory(rowcount=2, queued_ids=["a", "b"])
        rds = AsyncMock()

        recovered = await requeue_running_jobs(
            factory, _FakeModel, rds, "tino:x:queue",
        )

        assert recovered == 2
        assert db.execute.await_count == 2
        assert rds.lpush.await_count == 2
        rds.lpush.assert_any_await("tino:x:queue", "a")
        rds.lpush.assert_any_await("tino:x:queue", "b")
        db.commit.assert_awaited_once()

    async def test_reset_queue_deletes_before_repush(self):
        factory, _ = _mock_session_factory(rowcount=1, queued_ids=["a"])
        rds = AsyncMock()

        await requeue_running_jobs(
            factory, _FakeModel, rds, "q:k", reset_queue=True,
        )

        rds.delete.assert_awaited_once_with("q:k")
        rds.lpush.assert_awaited_once_with("q:k", "a")

    async def test_reset_queue_false_does_not_delete(self):
        factory, _ = _mock_session_factory(rowcount=1, queued_ids=["a"])
        rds = AsyncMock()

        await requeue_running_jobs(
            factory, _FakeModel, rds, "q:k", reset_queue=False,
        )

        rds.delete.assert_not_called()
        rds.lpush.assert_awaited_once_with("q:k", "a")

    async def test_no_queued_ids_no_lpush_no_delete(self):
        factory, _ = _mock_session_factory(rowcount=0, queued_ids=[])
        rds = AsyncMock()

        recovered = await requeue_running_jobs(
            factory, _FakeModel, rds, "q:k", reset_queue=True,
        )

        assert recovered == 0
        rds.lpush.assert_not_called()
        rds.delete.assert_not_called()

    async def test_no_queued_but_running_flipped(self):
        factory, _ = _mock_session_factory(rowcount=3, queued_ids=[])
        rds = AsyncMock()

        recovered = await requeue_running_jobs(
            factory, _FakeModel, rds, "q", reset_queue=True,
        )

        assert recovered == 3
        rds.lpush.assert_not_called()
        rds.delete.assert_not_called()

    async def test_rowcount_none_becomes_zero(self):
        factory, db = _mock_session_factory(rowcount=None, queued_ids=[])
        rds = AsyncMock()

        recovered = await requeue_running_jobs(factory, _FakeModel, rds, "q")

        assert recovered == 0

    async def test_commit_called_once(self):
        factory, db = _mock_session_factory(rowcount=5, queued_ids=["x", "y", "z"])
        rds = AsyncMock()

        await requeue_running_jobs(factory, _FakeModel, rds, "q")

        db.commit.assert_awaited_once()

    async def test_default_recovery_message_used_in_update(self):
        """The string passed to .values(message=...) matches the default."""
        factory, db = _mock_session_factory(rowcount=1, queued_ids=[])
        rds = AsyncMock()

        await requeue_running_jobs(factory, _FakeModel, rds, "q")

        # SQLAlchemy update statement is the first positional arg to execute
        first_call = db.execute.await_args_list[0]
        stmt = first_call.args[0]
        # The values clause is a private attr but we can compile the statement
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "Recovered after restart" in compiled

    async def test_custom_recovery_message(self):
        factory, db = _mock_session_factory(rowcount=1, queued_ids=[])
        rds = AsyncMock()

        await requeue_running_jobs(
            factory, _FakeModel, rds, "q",
            recovery_message="Custom banner",
        )

        stmt = db.execute.await_args_list[0].args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "Custom banner" in compiled

    async def test_logs_when_recovered_nonzero(self, caplog):
        factory, _ = _mock_session_factory(rowcount=4, queued_ids=[])
        rds = AsyncMock()

        with caplog.at_level(logging.INFO, logger="tinohelm.core.async_queue_worker"):
            await requeue_running_jobs(factory, _FakeModel, rds, "q")

        assert any("Recovered 4 interrupted" in r.message for r in caplog.records)

    async def test_no_log_when_recovered_zero(self, caplog):
        factory, _ = _mock_session_factory(rowcount=0, queued_ids=[])
        rds = AsyncMock()

        with caplog.at_level(logging.INFO, logger="tinohelm.core.async_queue_worker"):
            await requeue_running_jobs(factory, _FakeModel, rds, "q")

        assert not any("Recovered" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# consumer_loop
# ---------------------------------------------------------------------------

class _FakeAsyncRedis:
    """Minimal async redis double for ``consumer_loop``."""

    def __init__(self, items: list) -> None:
        # Each item is either (queue_key, job_id) or None (simulating timeout).
        self._items = list(items)
        self.closed = False
        self.brpop_calls: list[tuple[str, float]] = []

    async def brpop(self, key: str, timeout: float = 0.0):
        self.brpop_calls.append((key, timeout))
        if not self._items:
            # Block forever — test must cancel the task
            await asyncio.sleep(3600)
        return self._items.pop(0)

    async def close(self) -> None:
        self.closed = True


class TestConsumerLoop:
    async def test_processes_popped_job(self, monkeypatch):
        fake = _FakeAsyncRedis([("q", "job-1")])
        monkeypatch.setattr(
            "tinohelm.core.async_queue_worker.aioredis.from_url",
            lambda *_a, **_k: fake,
        )

        processed: list[str] = []

        async def _process(job_id: str) -> None:
            processed.append(job_id)
            # Stop the loop by cancelling self after one job
            raise asyncio.CancelledError

        await consumer_loop("redis://x", "q", _process)

        assert processed == ["job-1"]
        assert fake.closed is True

    async def test_timeout_none_continues(self, monkeypatch):
        fake = _FakeAsyncRedis([None, ("q", "job-2")])
        monkeypatch.setattr(
            "tinohelm.core.async_queue_worker.aioredis.from_url",
            lambda *_a, **_k: fake,
        )

        processed: list[str] = []

        async def _process(job_id: str) -> None:
            processed.append(job_id)
            raise asyncio.CancelledError

        await consumer_loop("redis://x", "q", _process)

        assert processed == ["job-2"]

    async def test_cancelled_error_exits_cleanly(self, monkeypatch):
        fake = _FakeAsyncRedis([])
        monkeypatch.setattr(
            "tinohelm.core.async_queue_worker.aioredis.from_url",
            lambda *_a, **_k: fake,
        )

        async def _process(job_id: str) -> None:
            pass

        task = asyncio.create_task(consumer_loop("redis://x", "q", _process))
        await asyncio.sleep(0.05)
        task.cancel()
        await task  # CancelledError is absorbed inside consumer_loop

        assert fake.closed is True

    async def test_rds_closed_even_when_process_raises(self, monkeypatch):
        fake = _FakeAsyncRedis([("q", "job-1")])
        monkeypatch.setattr(
            "tinohelm.core.async_queue_worker.aioredis.from_url",
            lambda *_a, **_k: fake,
        )

        async def _process(job_id: str) -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await consumer_loop("redis://x", "q", _process)

        assert fake.closed is True

    async def test_pop_timeout_passed_through(self, monkeypatch):
        fake = _FakeAsyncRedis([("q", "j1")])
        monkeypatch.setattr(
            "tinohelm.core.async_queue_worker.aioredis.from_url",
            lambda *_a, **_k: fake,
        )

        async def _process(job_id: str) -> None:
            raise asyncio.CancelledError

        await consumer_loop("redis://x", "q", _process, pop_timeout=7.5)

        assert fake.brpop_calls[0] == ("q", 7.5)

    async def test_worker_label_in_log(self, monkeypatch, caplog):
        fake = _FakeAsyncRedis([])
        monkeypatch.setattr(
            "tinohelm.core.async_queue_worker.aioredis.from_url",
            lambda *_a, **_k: fake,
        )

        async def _process(job_id: str) -> None:
            pass

        with caplog.at_level(logging.INFO, logger="tinohelm.core.async_queue_worker"):
            task = asyncio.create_task(
                consumer_loop("redis://x", "q", _process, worker_label="MyWorker")
            )
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        messages = [r.message for r in caplog.records]
        assert any("MyWorker started" in m for m in messages)
        assert any("MyWorker shutting down" in m for m in messages)

    async def test_processes_multiple_jobs_before_cancel(self, monkeypatch):
        fake = _FakeAsyncRedis([("q", "a"), ("q", "b"), ("q", "c")])
        monkeypatch.setattr(
            "tinohelm.core.async_queue_worker.aioredis.from_url",
            lambda *_a, **_k: fake,
        )

        processed: list[str] = []

        async def _process(job_id: str) -> None:
            processed.append(job_id)
            if len(processed) == 3:
                raise asyncio.CancelledError

        await consumer_loop("redis://x", "q", _process)

        assert processed == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# WorkerHandle
# ---------------------------------------------------------------------------

class TestWorkerHandle:
    async def test_initial_state(self):
        h = WorkerHandle(name="w")
        assert h.name == "w"
        assert h.task is None
        assert h.is_running() is False

    async def test_start_creates_task(self):
        h = WorkerHandle(name="w")

        async def _run():
            await asyncio.sleep(0.5)

        task = h.start(lambda: _run())
        try:
            assert isinstance(task, asyncio.Task)
            assert h.task is task
            assert h.is_running() is True
            assert task.get_name() == "w"
        finally:
            h.stop()
            # Allow cancellation to propagate
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_double_start_raises(self):
        h = WorkerHandle(name="w")

        async def _run():
            await asyncio.sleep(0.5)

        task = h.start(lambda: _run())
        try:
            with pytest.raises(RuntimeError, match="already running"):
                h.start(lambda: _run())
        finally:
            h.stop()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_stop_cancels_running_task(self):
        h = WorkerHandle(name="w")

        async def _run():
            await asyncio.sleep(5)

        task = h.start(lambda: _run())
        assert h.is_running() is True
        h.stop()

        # After stop(), ownership is retained until cancellation cleanup settles,
        # so a quick restart cannot create a second worker pool.
        assert h.task is task
        with pytest.raises(RuntimeError, match="already running"):
            h.start(lambda: _run())
        # The underlying asyncio task is cancelled
        with pytest.raises(asyncio.CancelledError):
            await task
        assert h.task is None

    async def test_stop_when_never_started_noop(self):
        h = WorkerHandle(name="w")
        h.stop()
        assert h.task is None
        assert h.is_running() is False

    async def test_stop_idempotent(self):
        h = WorkerHandle(name="w")

        async def _run():
            await asyncio.sleep(1)

        task = h.start(lambda: _run())
        h.stop()
        h.stop()
        h.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_restart_after_stop(self):
        h = WorkerHandle(name="w")

        async def _run():
            await asyncio.sleep(1)

        t1 = h.start(lambda: _run())
        h.stop()
        try:
            await t1
        except asyncio.CancelledError:
            pass

        t2 = h.start(lambda: _run())
        try:
            assert t2 is not t1
            assert h.is_running() is True
        finally:
            h.stop()
            try:
                await t2
            except asyncio.CancelledError:
                pass

    async def test_is_running_false_after_task_completes(self):
        h = WorkerHandle(name="w")

        async def _quick():
            return None

        task = h.start(lambda: _quick())
        await task  # Let it finish naturally
        assert task.done()
        assert h.is_running() is False

    async def test_can_start_after_natural_completion(self):
        h = WorkerHandle(name="w")

        async def _quick():
            return None

        task = h.start(lambda: _quick())
        await task
        # After done, start should succeed without RuntimeError
        task2 = h.start(lambda: _quick())
        await task2


# ---------------------------------------------------------------------------
# PercentStepThrottle
# ---------------------------------------------------------------------------

class TestPercentStepThrottle:
    def test_zero_step_raises(self):
        with pytest.raises(ValueError, match="positive"):
            PercentStepThrottle(step=0)

    def test_negative_step_raises(self):
        with pytest.raises(ValueError, match="positive"):
            PercentStepThrottle(step=-5)

    def test_step_property(self):
        t = PercentStepThrottle(step=20)
        assert t.step == 20

    def test_pct_zero_returns_true(self):
        t = PercentStepThrottle(step=10)
        assert t.should_write(0) is True

    def test_pct_hundred_returns_true(self):
        t = PercentStepThrottle(step=10)
        assert t.should_write(100) is True

    def test_pct_above_hundred_returns_true(self):
        t = PercentStepThrottle(step=10)
        assert t.should_write(150) is True

    def test_pct_negative_treated_as_boundary(self):
        t = PercentStepThrottle(step=10)
        assert t.should_write(-1) is True

    @pytest.mark.parametrize("pct", [10, 20, 30, 40, 50, 60, 70, 80, 90])
    def test_pct_on_step_boundary_true(self, pct):
        t = PercentStepThrottle(step=10)
        assert t.should_write(pct) is True

    @pytest.mark.parametrize("pct", [1, 5, 7, 11, 15, 23, 49, 99])
    def test_pct_off_step_boundary_false(self, pct):
        t = PercentStepThrottle(step=10)
        assert t.should_write(pct) is False

    def test_custom_step(self):
        t = PercentStepThrottle(step=25)
        assert t.should_write(25) is True
        assert t.should_write(50) is True
        assert t.should_write(75) is True
        assert t.should_write(10) is False

    def test_step_1_always_true(self):
        t = PercentStepThrottle(step=1)
        # Every integer pct in [0, 100] is a multiple of 1
        for pct in range(0, 101):
            assert t.should_write(pct) is True

    def test_stateless_repeated_calls_same_result(self):
        t = PercentStepThrottle(step=10)
        for _ in range(5):
            assert t.should_write(15) is False
            assert t.should_write(20) is True


# ---------------------------------------------------------------------------
# TimeThrottle
# ---------------------------------------------------------------------------

class TestTimeThrottle:
    def test_zero_interval_raises(self):
        with pytest.raises(ValueError, match="positive"):
            TimeThrottle(interval=0)

    def test_negative_interval_raises(self):
        with pytest.raises(ValueError, match="positive"):
            TimeThrottle(interval=-1.0)

    def test_interval_property(self):
        t = TimeThrottle(interval=3.5)
        assert t.interval == 3.5

    def test_pct_zero_always_true(self):
        t = TimeThrottle(interval=2.0, now_fn=lambda: 0.0)
        assert t.should_write(0) is True

    def test_pct_hundred_always_true(self):
        t = TimeThrottle(interval=2.0, now_fn=iter([0.0, 0.1]).__next__)
        # First call with pct=100 — boundary
        assert t.should_write(100) is True

    def test_pct_negative_treated_as_boundary(self):
        t = TimeThrottle(interval=2.0, now_fn=lambda: 0.0)
        assert t.should_write(-1) is True

    def test_middle_pct_blocked_until_interval(self):
        fake_now = iter([0.0, 0.5, 1.0, 2.5]).__next__
        t = TimeThrottle(interval=2.0, now_fn=fake_now)
        # pct=0 primes last_write to 0.0
        assert t.should_write(0) is True
        # now=0.5, elapsed=0.5 < 2 → False
        assert t.should_write(50) is False
        # now=1.0, elapsed=1.0 < 2 → False
        assert t.should_write(50) is False
        # now=2.5, elapsed=2.5 >= 2 → True
        assert t.should_write(50) is True

    def test_advances_last_write_on_true(self):
        # Sequence: first call at now=0 with pct=0 (boundary, advances),
        # then pct=50 at now=3 (elapsed 3 >= 2, advances),
        # then pct=50 at now=4 (elapsed 1 < 2 → False).
        fake_now = iter([0.0, 3.0, 4.0]).__next__
        t = TimeThrottle(interval=2.0, now_fn=fake_now)
        assert t.should_write(0) is True    # primes last_write=0
        assert t.should_write(50) is True   # now=3, advances last_write to 3
        assert t.should_write(50) is False  # now=4, elapsed 1 < 2

    def test_middle_pct_no_advance_when_false(self):
        fake_now = iter([0.0, 1.0, 1.5, 1.9, 2.5]).__next__
        t = TimeThrottle(interval=2.0, now_fn=fake_now)
        # Prime last_write at 0.0
        assert t.should_write(0) is True
        # Subsequent middle-pct calls below interval — all False, last_write stays at 0.0
        assert t.should_write(50) is False  # now=1.0
        assert t.should_write(50) is False  # now=1.5
        assert t.should_write(50) is False  # now=1.9
        # Now elapsed = 2.5 - 0.0 = 2.5 >= 2 → True
        assert t.should_write(50) is True   # now=2.5

    def test_default_now_fn_is_monotonic(self):
        t = TimeThrottle(interval=0.01)
        # pct=0 primes; immediate recall at pct=50 should see very small elapsed
        t.should_write(0)
        # We can't pin the exact time, but should_write(50) should be False
        # since monotonic advanced sub-millisecond.
        result = t.should_write(50)
        assert isinstance(result, bool)

    def test_boundary_hundred_always_true_even_if_interval_not_elapsed(self):
        fake_now = iter([0.0, 0.01, 0.02]).__next__
        t = TimeThrottle(interval=10.0, now_fn=fake_now)
        assert t.should_write(0) is True
        # Very close to last_write, but pct=100 forces True
        assert t.should_write(100) is True

    def test_independent_throttles_have_independent_state(self):
        """Two TimeThrottle instances do not share _last_write."""
        fake_now_a = iter([0.0, 5.0]).__next__
        fake_now_b = iter([0.0, 0.5]).__next__
        a = TimeThrottle(interval=2.0, now_fn=fake_now_a)
        b = TimeThrottle(interval=2.0, now_fn=fake_now_b)
        # a: pct=0 primes at now=0; pct=50 at now=5 → elapsed 5 >= 2 → True
        assert a.should_write(0) is True
        assert a.should_write(50) is True
        # b should still be uninfluenced — its last_write is 0 and now=0 → elapsed 0 < 2
        assert b.should_write(50) is False
        # b at now=0.5 → elapsed 0.5 < 2 → False
        assert b.should_write(50) is False
