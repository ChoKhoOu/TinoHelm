"""Tests for ``tinohelm.data.worker`` — data-fetch queue worker."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinohelm.data import worker as dw


# ---------------------------------------------------------------------------
# Module-level contracts
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_queue_key(self):
        assert dw.QUEUE_KEY == "tino:data:queue"

    def test_progress_throttle_interval_is_two_seconds(self):
        # Preserved from the original hand-written worker's 2.0s DB-write cap
        assert dw.PROGRESS_THROTTLE_INTERVAL == 2.0

    def test_handle_is_worker_handle(self):
        from tinohelm.core.async_queue_worker import WorkerHandle
        assert isinstance(dw._handle, WorkerHandle)
        assert dw._handle.name == "data-fetch-worker"

    def test_handle_starts_not_running(self):
        assert dw._handle.is_running() is False

    def test_public_api_preserved(self):
        # These four names + QUEUE_KEY are consumed by api/app.py
        # and api/routes/data.py. Keep the wire intact.
        assert callable(dw.enqueue_job)
        assert callable(dw.recover_interrupted_jobs)
        assert callable(dw.start_data_worker)
        assert callable(dw.stop_data_worker)


# ---------------------------------------------------------------------------
# enqueue_job delegates to shared helper
# ---------------------------------------------------------------------------

class TestEnqueueJob:
    async def test_lpushes_to_data_queue(self):
        rds = AsyncMock()
        await dw.enqueue_job(rds, "job-42")
        rds.lpush.assert_awaited_once_with("tino:data:queue", "job-42")

    async def test_handles_multiple_calls(self):
        rds = AsyncMock()
        await dw.enqueue_job(rds, "a")
        await dw.enqueue_job(rds, "b")
        assert rds.lpush.await_count == 2


# ---------------------------------------------------------------------------
# recover_interrupted_jobs delegates with reset_queue=True
# ---------------------------------------------------------------------------

class TestRecoverInterruptedJobs:
    async def test_invokes_shared_with_reset_queue_true(self, monkeypatch):
        called: dict = {}

        async def _fake_requeue(
            factory, model, rds, key, *, reset_queue=False, recovery_message=""
        ):
            called["factory"] = factory
            called["model"] = model
            called["rds"] = rds
            called["key"] = key
            called["reset_queue"] = reset_queue
            called["recovery_message"] = recovery_message
            return 7

        monkeypatch.setattr(dw, "requeue_running_jobs", _fake_requeue)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "FAKE_FACTORY")
        recover = AsyncMock(return_value=0)
        monkeypatch.setattr(dw, "_recover_pending_ingest_rollbacks_on_startup", recover)

        rds = AsyncMock()
        count = await dw.recover_interrupted_jobs(rds)

        assert count == 7
        recover.assert_awaited_once()
        assert called["model"] is dw.DataFetchJob
        assert called["rds"] is rds
        assert called["key"] == "tino:data:queue"
        assert called["reset_queue"] is True
        # factory is resolved at call time
        assert called["factory"] == "FAKE_FACTORY"


# ---------------------------------------------------------------------------
# _process_job — DB + Redis + pipeline integration
# ---------------------------------------------------------------------------

class _FakeSessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _make_session_factory(*, initial_job, commit_spy=None):
    """Build a factory that yields a fresh AsyncMock db each time.

    - First enter: queued-job lookup before acquiring the catalog lock.
    - Second enter after a successful lookup: atomic queued->running claim update.
    - Third enter after a successful claim: load job params.
    - Subsequent enters: guarded progress/terminal updates.
    """
    calls = {"count": 0}
    claim_rowcount = 1 if initial_job is not None and initial_job.status == "queued" else 0

    def factory():
        calls["count"] += 1
        db = AsyncMock()
        if calls["count"] == 1:
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value=initial_job if claim_rowcount == 1 else None)
            db.execute = AsyncMock(return_value=result)
        elif calls["count"] == 2 and claim_rowcount == 1:
            result = MagicMock()
            result.rowcount = claim_rowcount
            db.execute = AsyncMock(return_value=result)
        elif calls["count"] == 3 and claim_rowcount == 1:
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value=initial_job)
            db.execute = AsyncMock(return_value=result)
        else:
            async def _execute(stmt):
                result = MagicMock()
                if "SELECT data_fetch_jobs.status" in str(stmt):
                    result.scalar_one_or_none = MagicMock(return_value="running")
                else:
                    result.rowcount = 1
                return result
            db.execute = AsyncMock(side_effect=_execute)
        if commit_spy is not None:
            async def _commit():
                commit_spy.append(calls["count"])
            db.commit = _commit
        return _FakeSessionCtx(db)

    return factory, calls


def _fake_pipeline_result(objects_count: int = 42) -> SimpleNamespace:
    return SimpleNamespace(objects_count=objects_count)


class _FakePipeline:
    def __init__(self, *, result: SimpleNamespace, progress_calls: list | None = None):
        self._result = result
        self._progress_calls = progress_calls if progress_calls is not None else []

    async def ingest(self, *, progress_cb, **_kwargs):
        # Fire a few progress updates, then return
        await progress_cb(0, "start")
        await progress_cb(50, "half")
        await progress_cb(100, "done")
        self._progress_calls.extend([(0, "start"), (50, "half"), (100, "done")])
        return self._result


class TestProcessJob:
    async def test_missing_job_returns_without_work(self, monkeypatch):
        factory, _ = _make_session_factory(initial_job=None)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        fake_rds.close = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        # Pipeline should never be constructed
        pipeline_constructed = []
        import tinohelm.data.pipeline as pkg_pipeline

        class _Boom(pkg_pipeline.BinanceVisionPipeline):
            def __init__(self, *a, **k):
                pipeline_constructed.append(True)

        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _Boom)

        await dw._process_job("nope", "redis://x", "/cat")

        assert pipeline_constructed == []
        fake_rds.close.assert_awaited()
        # No completion event should have been published
        fake_rds.publish.assert_not_called()

    async def test_cancelled_job_returns_without_work(self, monkeypatch):
        job = SimpleNamespace(
            status="cancelled",
            symbol="BTC", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        pipeline_constructed = []
        import tinohelm.data.pipeline as pkg_pipeline
        class _Boom(pkg_pipeline.BinanceVisionPipeline):
            def __init__(self, *a, **k):
                pipeline_constructed.append(True)
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _Boom)

        await dw._process_job("jid", "redis://x", "/cat")

        assert pipeline_constructed == []
        fake_rds.publish.assert_not_called()

    async def test_running_duplicate_queue_entry_returns_without_work(self, monkeypatch):
        job = SimpleNamespace(
            status="running",
            symbol="BTC", data_type="aggTrades", interval=None,
            start_date="2025-01-01", end_date="2025-01-01", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)
        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        pipeline_constructed = []
        import tinohelm.data.pipeline as pkg_pipeline

        class _Boom(pkg_pipeline.BinanceVisionPipeline):
            def __init__(self, *a, **k):
                pipeline_constructed.append(True)

        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _Boom)

        await dw._process_job("jid", "redis://x", "/cat")

        assert pipeline_constructed == []
        fake_rds.publish.assert_not_called()

    async def test_cancelled_after_claim_before_lock_skips_ingest(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        calls = {"count": 0}

        def factory():
            calls["count"] += 1
            db = AsyncMock()
            if calls["count"] == 1:
                result = MagicMock()
                result.rowcount = 1
                db.execute = AsyncMock(return_value=result)
            elif calls["count"] == 2:
                result = MagicMock()
                result.scalar_one_or_none = MagicMock(return_value=job)
                db.execute = AsyncMock(return_value=result)
            else:
                result = MagicMock()
                result.scalar_one_or_none = MagicMock(return_value="cancelled")
                db.execute = AsyncMock(return_value=result)
            return _FakeSessionCtx(db)

        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)
        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        pipeline_constructed = []
        import tinohelm.data.pipeline as pkg_pipeline

        class _Boom(pkg_pipeline.BinanceVisionPipeline):
            def __init__(self, *a, **k):
                pipeline_constructed.append(True)

        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _Boom)

        await dw._process_job("job-cancelled-before-lock", "redis://x", "/cat")

        assert pipeline_constructed == []
        fake_rds.publish.assert_not_called()

    async def test_waits_for_catalog_lock_before_claiming_running(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        events: list[str] = []
        lock_waiting = asyncio.Event()
        release_lock = asyncio.Event()

        class _BlockingLock:
            async def __aenter__(self):
                events.append("lock-waiting")
                lock_waiting.set()
                await release_lock.wait()
                events.append("lock-acquired")

            async def __aexit__(self, *_exc):
                return False

        monkeypatch.setattr(dw, "_get_catalog_lock", lambda _key: _BlockingLock())

        def factory():
            db = AsyncMock()

            async def _execute(stmt):
                text = str(stmt)
                result = MagicMock()
                if "UPDATE data_fetch_jobs" in text:
                    events.append("update")
                    result.rowcount = 1
                elif "SELECT data_fetch_jobs.status" in text:
                    result.scalar_one_or_none = MagicMock(return_value="running")
                else:
                    result.scalar_one_or_none = MagicMock(return_value=job)
                return result

            db.execute = AsyncMock(side_effect=_execute)
            return _FakeSessionCtx(db)

        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)
        fake_rds = AsyncMock()
        monkeypatch.setattr(dw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        class _NoProgressPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                return _fake_pipeline_result(objects_count=3)

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _NoProgressPipeline)

        task = asyncio.create_task(dw._process_job("job-lock-wait", "redis://x", "/cat"))
        await asyncio.wait_for(lock_waiting.wait(), timeout=1)
        assert "update" not in events
        release_lock.set()
        await task

    async def test_cancelled_during_processing_is_not_overwritten_completed(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        calls = {"count": 0}
        terminal_statements = []

        def factory():
            calls["count"] += 1
            db = AsyncMock()
            if calls["count"] == 1:
                result = MagicMock()
                result.rowcount = 1
                db.execute = AsyncMock(return_value=result)
            elif calls["count"] == 2:
                result = MagicMock()
                result.scalar_one_or_none = MagicMock(return_value=job)
                db.execute = AsyncMock(return_value=result)
            else:
                async def _execute(stmt):
                    result = MagicMock()
                    if "SELECT data_fetch_jobs.status" in str(stmt):
                        result.scalar_one_or_none = MagicMock(return_value="running")
                    else:
                        result.rowcount = 0
                        terminal_statements.append(stmt)
                    return result

                db.execute = AsyncMock(side_effect=_execute)
            return _FakeSessionCtx(db)

        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)
        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _NoProgressPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                return _fake_pipeline_result(objects_count=7)

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _NoProgressPipeline)

        await dw._process_job("job-cancelled-mid-flight", "redis://x", "/cat")

        assert terminal_statements, "worker should attempt a guarded terminal update"
        assert all(
            "WHERE data_fetch_jobs.job_id =" in str(stmt)
            and "AND data_fetch_jobs.status =" in str(stmt)
            for stmt in terminal_statements
        )
        fake_rds.publish.assert_not_called()

    async def test_happy_path_runs_pipeline_and_publishes_completion(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, session_calls = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        fake_result = _fake_pipeline_result(objects_count=99)
        progress_calls: list = []
        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(
            pkg_pipeline,
            "BinanceVisionPipeline",
            lambda **_k: _FakePipeline(result=fake_result, progress_calls=progress_calls),
        )

        await dw._process_job("job-77", "redis://x", "/cat")

        # Progress callbacks each published + completion event published
        publish_topics = [c.args[0] for c in fake_rds.publish.await_args_list]
        assert "tino:data:progress:job-77" in publish_topics
        assert "tino:data:events" in publish_topics

        # Final "completed" event payload
        last_call = fake_rds.publish.await_args_list[-1]
        assert last_call.args[0] == "tino:data:events"
        payload = json.loads(last_call.args[1])
        assert payload["type"] == "data.fetch.completed"
        assert payload["job_id"] == "job-77"
        assert payload["symbol"] == "BTCUSDT"
        assert payload["objects_count"] == 99

        # Pipeline saw all three progress callbacks
        assert progress_calls == [(0, "start"), (50, "half"), (100, "done")]

        # rds is closed in finally
        fake_rds.close.assert_awaited()

    async def test_cancellation_during_completed_terminal_update_still_marks_completed(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _NoProgressPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                return _fake_pipeline_result(objects_count=11)

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _NoProgressPipeline)

        terminal_started = asyncio.Event()

        async def terminal_update(_factory, _job_id, _values):
            terminal_started.set()
            await asyncio.sleep(0.01)
            return True

        monkeypatch.setattr(dw, "_guarded_terminal_update", terminal_update)

        task = asyncio.create_task(dw._process_job("job-terminal-cancel", "redis://x", "/cat"))
        await terminal_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        final = fake_rds.publish.await_args_list[-1]
        assert final.args[0] == "tino:data:events"
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.completed"
        assert payload["job_id"] == "job-terminal-cancel"

    async def test_terminal_failure_after_shutdown_cancellation_preserves_cancellation(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        fake_rds.publish.side_effect = RuntimeError("redis publish down")
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _NoProgressPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                return _fake_pipeline_result(objects_count=5)

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _NoProgressPipeline)

        terminal_started = asyncio.Event()
        release_terminal = asyncio.Event()
        cancellation_cleared = asyncio.Event()
        original_clear = dw._clear_current_task_cancellation

        def clear_and_signal():
            cancellation_cleared.set()
            return original_clear()

        monkeypatch.setattr(dw, "_clear_current_task_cancellation", clear_and_signal)

        async def terminal_update(_factory, _job_id, _values):
            terminal_started.set()
            await release_terminal.wait()
            return True

        monkeypatch.setattr(dw, "_guarded_terminal_update", terminal_update)

        task = asyncio.create_task(dw._process_job("job-terminal-publish-fails-after-cancel", "redis://x", "/cat"))
        await terminal_started.wait()
        task.cancel()
        await asyncio.wait_for(cancellation_cleared.wait(), timeout=1)
        release_terminal.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        fake_rds.publish.assert_awaited_once()
        fake_rds.close.assert_awaited()

    async def test_pipeline_post_commit_cancellation_still_marks_completed_then_exits(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _CommittedThenRecancelPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                current = asyncio.current_task()
                assert current is not None
                current.cancel()
                return _fake_pipeline_result(objects_count=13)

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _CommittedThenRecancelPipeline)

        with pytest.raises(asyncio.CancelledError):
            await dw._process_job("job-pipeline-recanceled", "redis://x", "/cat")

        final = fake_rds.publish.await_args_list[-1]
        assert final.args[0] == "tino:data:events"
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.completed"
        assert payload["job_id"] == "job-pipeline-recanceled"
        assert payload["objects_count"] == 13

    async def test_cancellation_during_failed_terminal_update_still_marks_failed_then_exits(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _BoomPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                raise RuntimeError("network down")

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _BoomPipeline)

        terminal_started = asyncio.Event()

        async def terminal_update(_factory, _job_id, _values):
            terminal_started.set()
            await asyncio.sleep(0.01)
            return True

        monkeypatch.setattr(dw, "_guarded_terminal_update", terminal_update)

        task = asyncio.create_task(dw._process_job("job-failed-terminal-cancel", "redis://x", "/cat"))
        await terminal_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        final = fake_rds.publish.await_args_list[-1]
        assert final.args[0] == "tino:data:events"
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.failed"
        assert payload["job_id"] == "job-failed-terminal-cancel"
        assert "network down" in payload["error"]

    async def test_pipeline_exception_marks_failed_and_publishes_failure(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _BoomPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                raise RuntimeError("network down")

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _BoomPipeline)

        await dw._process_job("job-X", "redis://x", "/cat")

        # Completion event published to "tino:data:events" with type=data.fetch.failed
        publish_calls = list(fake_rds.publish.await_args_list)
        final = publish_calls[-1]
        assert final.args[0] == "tino:data:events"
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.failed"
        assert payload["job_id"] == "job-X"
        assert "network down" in payload["error"]

        fake_rds.close.assert_awaited()

    async def test_progress_payload_shape(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="ETH", data_type="aggTrades", interval=None,
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _TracePipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, *, progress_cb, **_k):
                await progress_cb(33, "working")
                return _fake_pipeline_result(1)

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _TracePipeline)

        await dw._process_job("j1", "redis://x", "/cat")

        # Locate the progress publish with pct=33
        progress_published = [
            json.loads(c.args[1])
            for c in fake_rds.publish.await_args_list
            if c.args[0].startswith("tino:data:progress:")
        ]
        assert progress_published
        p = progress_published[0]
        assert p["type"] == "data.fetch.progress"
        assert p["job_id"] == "j1"
        assert p["symbol"] == "ETH"
        assert p["data_type"] == "aggTrades"
        assert p["progress"] == 33
        assert p["message"] == "working"
        assert "interval" not in p  # interval is None — key should be absent

    async def test_progress_interval_key_when_present(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="ETH", data_type="klines", interval="5m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        factory, _ = _make_session_factory(initial_job=job)
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(
            dw.aioredis, "from_url", lambda *_a, **_k: fake_rds,
        )

        class _TracePipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, *, progress_cb, **_k):
                await progress_cb(1, "early")
                return _fake_pipeline_result(1)

        import tinohelm.data.pipeline as pkg_pipeline
        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _TracePipeline)

        await dw._process_job("j1", "redis://x", "/cat")

        progress_published = [
            json.loads(c.args[1])
            for c in fake_rds.publish.await_args_list
            if c.args[0].startswith("tino:data:progress:")
        ]
        assert progress_published[0]["interval"] == "5m"


# ---------------------------------------------------------------------------
# start_data_worker / stop_data_worker
# ---------------------------------------------------------------------------

class TestWorkerLifecycle:
    async def test_start_launches_configured_parallel_consumers(self, monkeypatch):
        started: list[dict] = []

        async def _fake_consumer(redis_url, queue_key, process_job, *,
                                  pop_timeout=5.0, worker_label="queue-worker"):
            started.append({
                "redis_url": redis_url,
                "queue_key": queue_key,
                "worker_label": worker_label,
            })
            await asyncio.sleep(5)

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)
        monkeypatch.setattr(
            dw,
            "get_settings",
            lambda: SimpleNamespace(data=SimpleNamespace(job_concurrency=3)),
            raising=False,
        )
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://x", catalog_path="/cat")
        try:
            await asyncio.sleep(0.02)
            assert len(started) == 3
            assert {call["redis_url"] for call in started} == {"redis://x"}
            assert {call["queue_key"] for call in started} == {"tino:data:queue"}
        finally:
            dw.stop_data_worker()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_start_creates_task_via_handle(self, monkeypatch):
        # Replace consumer_loop with a coroutine that sleeps briefly
        started: dict = {}

        async def _fake_consumer(redis_url, queue_key, process_job, *,
                                  pop_timeout=5.0, worker_label="queue-worker"):
            started["redis_url"] = redis_url
            started["queue_key"] = queue_key
            started["worker_label"] = worker_label
            await asyncio.sleep(5)

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)

        # Reset the handle so this test is isolated
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://x", catalog_path="/cat")
        try:
            await asyncio.sleep(0.02)
            assert dw._handle.is_running() is True
            assert started["redis_url"] == "redis://x"
            assert started["queue_key"] == "tino:data:queue"
            assert started["worker_label"] == "Data-fetch worker"
        finally:
            dw.stop_data_worker()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_stop_cancels_running_task(self, monkeypatch):
        async def _fake_consumer(*_a, **_k):
            await asyncio.sleep(5)

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://x", catalog_path="/cat")
        await asyncio.sleep(0.01)
        dw.stop_data_worker()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert dw._handle.is_running() is False

    async def test_stop_idempotent_when_never_started(self, monkeypatch):
        dw._handle.stop()
        dw.stop_data_worker()  # No-op
        dw.stop_data_worker()  # Still no-op
        assert dw._handle.is_running() is False

    async def test_same_catalog_key_jobs_are_serialized(self):
        dw._catalog_locks.clear()
        order: list[str] = []
        lock_key = dw._catalog_lock_key("BTCUSDT-PERP", "aggTrades", None)

        async def _job(name: str):
            async with dw._get_catalog_lock(lock_key):
                order.append(f"{name}:start")
                await asyncio.sleep(0.02)
                order.append(f"{name}:end")

        await asyncio.gather(_job("a"), _job("b"))

        assert order in (
            ["a:start", "a:end", "b:start", "b:end"],
            ["b:start", "b:end", "a:start", "a:end"],
        )

    async def test_consumer_supervisor_restarts_failed_consumer(self, monkeypatch):
        attempts = 0
        restarted = asyncio.Event()

        async def _fake_consumer(*_a, **_k):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient redis error")
            restarted.set()
            await asyncio.sleep(5)

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)
        monkeypatch.setattr(
            dw,
            "get_settings",
            lambda: SimpleNamespace(data=SimpleNamespace(job_concurrency=1)),
            raising=False,
        )
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://x", catalog_path="/cat")
        try:
            await asyncio.wait_for(restarted.wait(), timeout=1)
            assert attempts == 2
            assert task.done() is False
        finally:
            dw.stop_data_worker()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_process_callback_gets_job_id(self, monkeypatch):
        """The coroutine factory passed into consumer_loop unwraps the job_id correctly."""
        captured: dict = {}

        async def _fake_consumer(redis_url, queue_key, process_job, **_k):
            # Invoke the process callback with a fake job id
            await process_job("job-captured")

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)

        async def _fake_process(job_id, redis_url, catalog_path):
            captured["job_id"] = job_id
            captured["redis_url"] = redis_url
            captured["catalog_path"] = catalog_path

        monkeypatch.setattr(dw, "_process_job", _fake_process)
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://r", catalog_path="/c")
        await task  # runs _fake_consumer which completes immediately

        assert captured == {
            "job_id": "job-captured",
            "redis_url": "redis://r",
            "catalog_path": "/c",
        }
