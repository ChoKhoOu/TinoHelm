"""Tests for ``tinohelm.research.worker`` — research-diagnosis queue worker."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinohelm.research import worker as rw


# ---------------------------------------------------------------------------
# Module-level contracts
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_queue_key(self):
        assert rw.QUEUE_KEY == "tino:research:queue"

    def test_progress_db_step_is_ten(self):
        # Preserved from the original hand-written worker: write DB every 10%
        assert rw.PROGRESS_DB_STEP == 10

    def test_handle_is_worker_handle(self):
        from tinohelm.core.async_queue_worker import WorkerHandle
        assert isinstance(rw._handle, WorkerHandle)
        assert rw._handle.name == "research-worker"

    def test_handle_starts_not_running(self):
        assert rw._handle.is_running() is False

    def test_public_api_preserved(self):
        # api/app.py and api/routes/research.py rely on these names.
        assert callable(rw.enqueue_job)
        assert callable(rw.recover_interrupted_jobs)
        assert callable(rw.start_research_worker)
        assert callable(rw.stop_research_worker)


# ---------------------------------------------------------------------------
# enqueue_job delegates to shared helper
# ---------------------------------------------------------------------------

class TestEnqueueJob:
    async def test_lpushes_to_research_queue(self):
        rds = AsyncMock()
        await rw.enqueue_job(rds, "job-42")
        rds.lpush.assert_awaited_once_with("tino:research:queue", "job-42")


# ---------------------------------------------------------------------------
# recover_interrupted_jobs delegates with reset_queue=False
# ---------------------------------------------------------------------------

class TestRecoverInterruptedJobs:
    async def test_invokes_shared_with_reset_queue_false(self, monkeypatch):
        called: dict = {}

        async def _fake_requeue(
            factory, model, rds, key, *, reset_queue=False, recovery_message=""
        ):
            called["model"] = model
            called["key"] = key
            called["reset_queue"] = reset_queue
            return 3

        monkeypatch.setattr(rw, "requeue_running_jobs", _fake_requeue)
        monkeypatch.setattr(rw, "get_session_factory", lambda: "FACT")

        count = await rw.recover_interrupted_jobs(AsyncMock())

        assert count == 3
        assert called["model"] is rw.ResearchJob
        assert called["key"] == "tino:research:queue"
        # Research preserves historical semantics: does NOT reset the Redis queue
        assert called["reset_queue"] is False


# ---------------------------------------------------------------------------
# _process_job — DB + Redis + generate_report integration
# ---------------------------------------------------------------------------

class _FakeSessionCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


def _make_session_factory(*, initial_job):
    calls = {"count": 0}

    def factory():
        calls["count"] += 1
        db = AsyncMock()
        if calls["count"] == 1:
            result = MagicMock()
            result.scalar_one_or_none = MagicMock(return_value=initial_job)
            db.execute = AsyncMock(return_value=result)
        else:
            db.execute = AsyncMock(return_value=MagicMock())
        return _FakeSessionCtx(db)

    return factory, calls


def _build_job(*, status: str = "queued", parameters_json=None):
    return SimpleNamespace(
        status=status,
        symbol="BTCUSDT",
        factor_name="momentum",
        data_type="bar",
        interval="1h",
        start_date="2025-01-01",
        end_date="2025-01-02",
        parameters_json=parameters_json or {},
    )


class TestProcessJob:
    async def test_missing_job_returns_without_work(self, monkeypatch):
        factory, _ = _make_session_factory(initial_job=None)
        monkeypatch.setattr(rw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(rw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        generate_called = []

        def _fake_generate(**_k):
            generate_called.append(True)
            return {}

        import tinohelm.research.report as pkg_report
        monkeypatch.setattr(pkg_report, "generate_report", _fake_generate)

        await rw._process_job("missing", "redis://x", "/cat")

        assert generate_called == []
        fake_rds.publish.assert_not_called()
        fake_rds.close.assert_awaited()

    async def test_cancelled_job_returns_without_work(self, monkeypatch):
        factory, _ = _make_session_factory(initial_job=_build_job(status="cancelled"))
        monkeypatch.setattr(rw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(rw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        import tinohelm.research.report as pkg_report
        called = []

        def _fake_generate(**_k):
            called.append(True)
            return {}

        monkeypatch.setattr(pkg_report, "generate_report", _fake_generate)

        await rw._process_job("j-cancel", "redis://x", "/cat")

        assert called == []
        fake_rds.publish.assert_not_called()

    async def test_happy_path_publishes_completion_with_rating(self, monkeypatch):
        factory, _ = _make_session_factory(
            initial_job=_build_job(parameters_json={
                "factor_params": {"window": 14},
                "forward_periods": [5, 10],
                "quantiles": 4,
                "shuffle_iterations": 100,
                "cross_symbols": ["ETHUSDT"],
                "param_scan": None,
                "fee_rate": 0.0003,
                "slippage_bps": 0.5,
            }),
        )
        monkeypatch.setattr(rw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(rw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        # generate_report is called via asyncio.to_thread — stub it to return
        # a report dict immediately.
        def _fake_generate(*, progress_cb=None, **_kwargs):
            # Fire a few sync progress callbacks; they are bridged to the loop
            # but we don't need to actually wait for them here.
            if progress_cb:
                progress_cb(0, "start")
                progress_cb(100, "done")
            return {
                "path": "/tmp/report.json",
                "report": {
                    "summary": {"rating": 2},
                    "verdict": {"overall": "usable"},
                },
            }

        import tinohelm.research.report as pkg_report
        monkeypatch.setattr(pkg_report, "generate_report", _fake_generate)

        await rw._process_job("job-XYZ", "redis://x", "/cat")

        # The completion event must be the last publish to tino:research:events
        event_publishes = [
            c for c in fake_rds.publish.await_args_list
            if c.args[0] == "tino:research:events"
        ]
        assert event_publishes, "expected a completion event publish"
        payload = json.loads(event_publishes[-1].args[1])
        assert payload["type"] == "research.completed"
        assert payload["job_id"] == "job-XYZ"
        assert payload["factor_name"] == "momentum"
        assert payload["symbol"] == "BTCUSDT"
        assert payload["rating"] == 2
        assert payload["verdict"] == {"overall": "usable"}

        fake_rds.close.assert_awaited()

    async def test_generate_report_exception_marks_failed(self, monkeypatch):
        factory, _ = _make_session_factory(initial_job=_build_job())
        monkeypatch.setattr(rw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(rw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        def _boom(**_k):
            raise ValueError("bad factor config")

        import tinohelm.research.report as pkg_report
        monkeypatch.setattr(pkg_report, "generate_report", _boom)

        await rw._process_job("job-bad", "redis://x", "/cat")

        event_publishes = [
            c for c in fake_rds.publish.await_args_list
            if c.args[0] == "tino:research:events"
        ]
        assert event_publishes
        payload = json.loads(event_publishes[-1].args[1])
        assert payload["type"] == "research.failed"
        assert payload["job_id"] == "job-bad"
        assert "bad factor config" in payload["error"]
        fake_rds.close.assert_awaited()

    async def test_defaults_applied_when_parameters_json_empty(self, monkeypatch):
        """Empty params → generate_report gets the documented defaults."""
        factory, _ = _make_session_factory(initial_job=_build_job(parameters_json={}))
        monkeypatch.setattr(rw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(rw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        captured_kwargs: dict = {}

        def _fake_generate(**kwargs):
            captured_kwargs.update(kwargs)
            return {
                "path": "/tmp/r.json",
                "report": {"summary": {"rating": 0}, "verdict": {}},
            }

        import tinohelm.research.report as pkg_report
        monkeypatch.setattr(pkg_report, "generate_report", _fake_generate)

        await rw._process_job("j1", "redis://x", "/cat")

        assert captured_kwargs["factor_params"] == {}
        assert captured_kwargs["forward_periods"] == [5, 15, 30]
        assert captured_kwargs["n_quantiles"] == 5
        assert captured_kwargs["shuffle_iterations"] == 1000
        assert captured_kwargs["cross_symbols"] is None
        assert captured_kwargs["param_scan_config"] is None
        assert captured_kwargs["fee_rate"] == 0.0004
        assert captured_kwargs["slippage_bps"] == 1.0
        assert captured_kwargs["catalog_path"] == "/cat"

    async def test_none_parameters_json_treated_as_empty(self, monkeypatch):
        """parameters_json=None should not crash — treat as {}."""
        factory, _ = _make_session_factory(
            initial_job=_build_job(parameters_json=None),
        )
        monkeypatch.setattr(rw, "get_session_factory", lambda: factory)

        fake_rds = AsyncMock()
        monkeypatch.setattr(rw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        def _fake_generate(**_k):
            return {
                "path": None,
                "report": {"summary": {"rating": 1}, "verdict": {}},
            }

        import tinohelm.research.report as pkg_report
        monkeypatch.setattr(pkg_report, "generate_report", _fake_generate)

        await rw._process_job("jN", "redis://x", "/cat")

        # Completion should fire even with None path
        event_publishes = [
            c for c in fake_rds.publish.await_args_list
            if c.args[0] == "tino:research:events"
        ]
        assert event_publishes
        payload = json.loads(event_publishes[-1].args[1])
        assert payload["type"] == "research.completed"


# ---------------------------------------------------------------------------
# start_research_worker / stop_research_worker
# ---------------------------------------------------------------------------

class TestWorkerLifecycle:
    async def test_start_creates_task_via_handle(self, monkeypatch):
        started: dict = {}

        async def _fake_consumer(redis_url, queue_key, process_job, *,
                                  pop_timeout=5.0, worker_label="queue-worker"):
            started["redis_url"] = redis_url
            started["queue_key"] = queue_key
            started["worker_label"] = worker_label
            await asyncio.sleep(5)

        monkeypatch.setattr(rw, "consumer_loop", _fake_consumer)
        rw._handle.stop()

        task = rw.start_research_worker(redis_url="redis://x", catalog_path="/cat")
        try:
            await asyncio.sleep(0.02)
            assert rw._handle.is_running() is True
            assert started["redis_url"] == "redis://x"
            assert started["queue_key"] == "tino:research:queue"
            assert started["worker_label"] == "Research worker"
        finally:
            rw.stop_research_worker()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_stop_cancels_running_task(self, monkeypatch):
        async def _fake_consumer(*_a, **_k):
            await asyncio.sleep(5)

        monkeypatch.setattr(rw, "consumer_loop", _fake_consumer)
        rw._handle.stop()

        task = rw.start_research_worker(redis_url="redis://x", catalog_path="/cat")
        await asyncio.sleep(0.01)
        rw.stop_research_worker()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rw._handle.is_running() is False

    async def test_stop_idempotent_when_never_started(self, monkeypatch):
        rw._handle.stop()
        rw.stop_research_worker()
        rw.stop_research_worker()
        assert rw._handle.is_running() is False

    async def test_process_callback_gets_job_id(self, monkeypatch):
        captured: dict = {}

        async def _fake_consumer(redis_url, queue_key, process_job, **_k):
            await process_job("research-job-1")

        monkeypatch.setattr(rw, "consumer_loop", _fake_consumer)

        async def _fake_process(job_id, redis_url, catalog_path):
            captured["job_id"] = job_id
            captured["redis_url"] = redis_url
            captured["catalog_path"] = catalog_path

        monkeypatch.setattr(rw, "_process_job", _fake_process)
        rw._handle.stop()

        task = rw.start_research_worker(redis_url="redis://R", catalog_path="/CAT")
        await task

        assert captured == {
            "job_id": "research-job-1",
            "redis_url": "redis://R",
            "catalog_path": "/CAT",
        }
