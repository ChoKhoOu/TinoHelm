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

        rds = AsyncMock()
        count = await dw.recover_interrupted_jobs(rds)

        assert count == 7
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

    - First enter: returns the job (select → scalar_one_or_none).
    - Subsequent enters (progress-write or completion): benign execute/commit.
    """
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        db = AsyncMock()
        if calls["count"] == 1 and initial_job is not None:
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value=initial_job)
            db.execute = AsyncMock(return_value=result)
        elif calls["count"] == 1 and initial_job is None:
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value=None)
            db.execute = AsyncMock(return_value=result)
        else:
            db.execute = AsyncMock(return_value=MagicMock())
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
