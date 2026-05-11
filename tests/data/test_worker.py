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
        assert callable(dw.stop_data_worker_and_wait)


# ---------------------------------------------------------------------------
# enqueue_job delegates to shared helper
# ---------------------------------------------------------------------------

class TestEnqueueJob:
    async def test_lpushes_wake_token_not_job_id(self):
        # Issue #164: scheduling truth lives in the DB, not Redis list order.
        # enqueue_job must emit a wake sentinel so the worker drains from DB —
        # it must NOT include the job_id as queue payload.
        rds = AsyncMock()
        await dw.enqueue_job(rds, "job-42")
        rds.lpush.assert_awaited_once()
        args = rds.lpush.await_args.args
        assert args[0] == "tino:data:queue"
        assert args[1] != "job-42", (
            "wake signal must not carry the job_id — drain picks it from the DB"
        )
        assert args[1] == dw.WAKE_TOKEN

    async def test_multiple_enqueues_each_push_wake_token(self):
        rds = AsyncMock()
        await dw.enqueue_job(rds, "a")
        await dw.enqueue_job(rds, "b")
        assert rds.lpush.await_count == 2
        assert all(call.args[1] == dw.WAKE_TOKEN for call in rds.lpush.await_args_list)


# ---------------------------------------------------------------------------
# claim_next_queued_job — DB picks next job, not Redis list
# ---------------------------------------------------------------------------

class TestClaimNextQueuedJob:
    """Issue #164 tracer: DB is the scheduling source of truth.

    ``claim_next_queued_job(factory)`` must atomically:
      - Pick the oldest ``status='queued'`` row (ORDER BY created_at, id)
      - Flip it to ``status='running'``
      - Return the claimed row, or ``None`` if no queued rows remain.
    """

    async def test_returns_none_when_no_queued_rows(self, monkeypatch):
        def factory():
            db = AsyncMock()
            # UPDATE ... RETURNING matches nothing
            result = MagicMock()
            result.rowcount = 0
            result.scalar_one_or_none = MagicMock(return_value=None)
            db.execute = AsyncMock(return_value=result)
            return _FakeSessionCtx(db)

        assert await dw.claim_next_queued_job(factory) is None

    async def test_claims_oldest_queued_job_and_returns_it(self, monkeypatch):
        picked_job_id = "old-job-1"
        claimed_job = SimpleNamespace(
            job_id=picked_job_id,
            status="running",
            symbol="BTCUSDT",
            data_type="klines",
            interval="1m",
            start_date="2025-01-01",
            end_date="2025-01-02",
            asset_class="um",
        )

        executed_statements: list[str] = []

        def factory():
            db = AsyncMock()

            async def _execute(stmt):
                text = str(stmt)
                executed_statements.append(text)
                result = MagicMock()
                # The first statement is the atomic claim (UPDATE); the second
                # reads the claimed row so the worker can process it.
                if "UPDATE" in text.upper():
                    result.rowcount = 1
                    result.scalar_one_or_none = MagicMock(return_value=picked_job_id)
                else:
                    result.scalar_one_or_none = MagicMock(return_value=claimed_job)
                return result

            db.execute = AsyncMock(side_effect=_execute)
            return _FakeSessionCtx(db)

        got = await dw.claim_next_queued_job(factory)

        assert got is claimed_job
        # The claim statement must atomically flip queued → running — i.e. the
        # WHERE clause must be gated on status='queued' so two racing consumers
        # can't both claim the same row.
        claim_stmt = executed_statements[0]
        assert "UPDATE" in claim_stmt.upper()
        assert "status" in claim_stmt.lower()
        # And it must select the oldest queued row as its target.
        assert "ORDER BY" in claim_stmt.upper()
        # SQLAlchemy parameterizes the limit (``LIMIT :param_1``), so just
        # require a LIMIT clause, not the literal "LIMIT 1".
        assert " LIMIT " in claim_stmt.upper() or "fetch_first_rows_only" in claim_stmt.lower()

    async def test_update_rowcount_zero_means_race_lost(self, monkeypatch):
        def factory():
            db = AsyncMock()
            # Guarded UPDATE returns rowcount=0 — another consumer won the race.
            result = MagicMock()
            result.rowcount = 0
            result.scalar_one_or_none = MagicMock(return_value=None)
            db.execute = AsyncMock(return_value=result)
            return _FakeSessionCtx(db)

        assert await dw.claim_next_queued_job(factory) is None


# ---------------------------------------------------------------------------
# drain_once — one wake token makes the worker keep claiming jobs until empty
# ---------------------------------------------------------------------------

class TestDrainOnce:
    """Issue #164 acceptance: one wake signal ⇒ many jobs processed.

    ``drain_once(process_job, redis_url, catalog_path)`` is the coroutine
    a consumer runs after BRPOP returns. It must loop, asking
    ``claim_next_queued_job`` for work, until the DB reports none.
    That makes one wake token sufficient to drain an arbitrarily deep
    backlog — the old "one LPUSH per job" contract no longer holds.
    """

    async def test_drains_until_no_more_queued_jobs(self, monkeypatch):
        jobs = [
            SimpleNamespace(job_id="j1", symbol="BTC", data_type="klines", interval="1m"),
            SimpleNamespace(job_id="j2", symbol="ETH", data_type="klines", interval="1m"),
            SimpleNamespace(job_id="j3", symbol="SOL", data_type="klines", interval="1m"),
        ]
        pending: list = list(jobs)

        async def fake_claim(_factory):
            return pending.pop(0) if pending else None

        processed: list[str] = []

        async def fake_process(job, redis_url: str, catalog_path: str) -> bool:
            processed.append(job.job_id)
            return True  # row reached terminal; drain continues

        monkeypatch.setattr(dw, "claim_next_queued_job", fake_claim)
        monkeypatch.setattr(dw, "_process_claimed_job", fake_process)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")

        n = await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        assert n == 3
        assert processed == ["j1", "j2", "j3"]

    async def test_returns_zero_when_nothing_queued(self, monkeypatch):
        async def fake_claim(_factory):
            return None

        processed: list = []

        async def fake_process(*_args, **_kwargs):
            processed.append(True)

        monkeypatch.setattr(dw, "claim_next_queued_job", fake_claim)
        monkeypatch.setattr(dw, "_process_claimed_job", fake_process)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")

        n = await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        assert n == 0
        assert processed == []

    async def test_process_exception_does_not_stop_drain(self, monkeypatch):
        """Best-effort batch semantics: one job failing in-pipeline must not
        break sibling claims. (The job body itself already writes failed
        status — see TestProcessJob — so drain's job is just to keep going.)
        """
        jobs = [
            SimpleNamespace(job_id="bad"),
            SimpleNamespace(job_id="good"),
        ]
        pending = list(jobs)

        async def fake_claim(_factory):
            return pending.pop(0) if pending else None

        processed: list[str] = []

        async def fake_process(job, redis_url: str, catalog_path: str) -> None:
            if job.job_id == "bad":
                raise RuntimeError("pipeline exploded")
            processed.append(job.job_id)

        monkeypatch.setattr(dw, "claim_next_queued_job", fake_claim)
        monkeypatch.setattr(dw, "_process_claimed_job", fake_process)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")

        n = await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        # Both jobs were attempted; the "good" one processed successfully.
        assert n == 2
        assert processed == ["good"]


# ---------------------------------------------------------------------------
# recover_interrupted_jobs delegates with reset_queue=True
# ---------------------------------------------------------------------------

class TestRecoverInterruptedJobs:
    async def test_flips_running_to_queued_and_clears_redis_list(self, monkeypatch):
        """Issue #164: recovery must not repush per-job payloads onto Redis.

        After rollout, Redis never stores job_ids — scheduling truth lives
        in the DB. Startup recovery therefore only needs to:
          1. flip every ``running`` row back to ``queued``
          2. clear whatever legacy payload Redis still holds
          3. nudge a worker awake if and only if queued work exists
        """
        flipped: dict = {}
        factory_marker = object()

        async def _fake_flip(factory, model):
            flipped["factory"] = factory
            flipped["model"] = model
            return 4  # Pretend 4 running rows flipped back to queued.

        monkeypatch.setattr(dw, "_flip_running_to_queued", _fake_flip)
        monkeypatch.setattr(dw, "_count_queued_jobs", AsyncMock(return_value=9))
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory_marker)
        monkeypatch.setattr(
            dw,
            "_recover_pending_ingest_rollbacks_on_startup",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            dw,
            "backfill_legacy_batch_ids",
            AsyncMock(return_value=0),
        )

        rds = AsyncMock()
        count = await dw.recover_interrupted_jobs(rds)

        assert count == 4
        assert flipped == {"factory": factory_marker, "model": dw.DataFetchJob}
        # Legacy Redis backlog is cleared, not replayed.
        rds.delete.assert_awaited_once_with("tino:data:queue")

    async def test_pushes_one_wake_token_when_queued_work_exists(self, monkeypatch):
        """With queued rows still present, recovery must wake exactly ONE
        consumer — not one wake token per job (PRD #162 decision #20)."""
        monkeypatch.setattr(dw, "_flip_running_to_queued", AsyncMock(return_value=3))
        monkeypatch.setattr(dw, "_count_queued_jobs", AsyncMock(return_value=12))
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")
        monkeypatch.setattr(
            dw,
            "_recover_pending_ingest_rollbacks_on_startup",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            dw,
            "backfill_legacy_batch_ids",
            AsyncMock(return_value=0),
        )

        rds = AsyncMock()
        await dw.recover_interrupted_jobs(rds)

        # One wake token, regardless of how many queued jobs exist.
        rds.lpush.assert_awaited_once_with("tino:data:queue", dw.WAKE_TOKEN)

    async def test_no_wake_when_nothing_queued(self, monkeypatch):
        """Empty DB ⇒ no wake token; the worker will wait on BRPOP until
        something new arrives, avoiding noise on clean restarts."""
        monkeypatch.setattr(dw, "_flip_running_to_queued", AsyncMock(return_value=0))
        monkeypatch.setattr(dw, "_count_queued_jobs", AsyncMock(return_value=0))
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")
        monkeypatch.setattr(
            dw,
            "_recover_pending_ingest_rollbacks_on_startup",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(
            dw,
            "backfill_legacy_batch_ids",
            AsyncMock(return_value=0),
        )

        rds = AsyncMock()
        await dw.recover_interrupted_jobs(rds)

        rds.lpush.assert_not_awaited()

    async def test_backfills_legacy_backlog_before_clearing_redis(
        self, monkeypatch
    ):
        """Issue #166: startup recovery must adopt legacy backlog into the
        new scheduler by backfilling ``batch_id`` before clearing Redis.

        Verifies:
          - ``backfill_legacy_batch_ids`` is invoked with the session factory.
          - It runs BEFORE ``rds.delete`` so legacy Redis tokens are only
            purged once DB-side adoption has succeeded (if the backfill
            raised, Redis would still carry the old wake signal for retry).
          - Its touched-row count is logged/returned alongside the flip count.
        """
        factory_marker = object()
        call_order: list[str] = []

        async def _fake_flip(_factory, _model):
            call_order.append("flip")
            return 2

        async def _fake_backfill(factory):
            call_order.append("backfill")
            assert factory is factory_marker
            return 5

        monkeypatch.setattr(dw, "_flip_running_to_queued", _fake_flip)
        monkeypatch.setattr(dw, "_count_queued_jobs", AsyncMock(return_value=7))
        monkeypatch.setattr(dw, "get_session_factory", lambda: factory_marker)
        monkeypatch.setattr(
            dw,
            "_recover_pending_ingest_rollbacks_on_startup",
            AsyncMock(return_value=0),
        )
        monkeypatch.setattr(dw, "backfill_legacy_batch_ids", _fake_backfill)

        rds = AsyncMock()

        async def _delete(_key):
            call_order.append("redis-delete")
            return 1

        rds.delete = _delete

        await dw.recover_interrupted_jobs(rds)

        # Backfill must run before we drop the legacy Redis list — otherwise
        # a crash mid-backfill could leave both DB legacy rows AND a wake
        # signal unreachable.
        assert "backfill" in call_order
        assert call_order.index("backfill") < call_order.index("redis-delete")

    async def test_startup_rollback_recovery_keeps_db_check_on_current_event_loop(
        self, monkeypatch, tmp_path
    ):
        storage = SimpleNamespace(catalog_root=tmp_path / "catalog")
        settings = SimpleNamespace(paths=SimpleNamespace(catalog=storage.catalog_root))
        calls: dict = {}

        async def fake_recover(catalog_path, *, storage):
            calls["loop"] = asyncio.get_running_loop()
            calls["catalog_path"] = catalog_path
            calls["storage"] = storage
            return 3

        async def fail_to_thread(*_args, **_kwargs):
            raise AssertionError("startup rollback recovery must not run inside asyncio.to_thread")

        monkeypatch.setattr(dw, "get_settings", lambda: settings)
        monkeypatch.setattr("tinohelm.data.storage.get_catalog_storage", lambda **_kwargs: storage)
        monkeypatch.setattr(
            "tinohelm.data.pipeline.recover_pending_ingest_rollbacks_async",
            fake_recover,
            raising=False,
        )
        monkeypatch.setattr(dw.asyncio, "to_thread", fail_to_thread)

        restored = await dw._recover_pending_ingest_rollbacks_on_startup()

        assert restored == 3
        assert calls == {
            "loop": asyncio.get_running_loop(),
            "catalog_path": storage.catalog_root,
            "storage": storage,
        }


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

    async def test_requeues_same_catalog_job_when_lock_busy_without_claiming_running(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        events: list[str] = []
        busy_lock = asyncio.Lock()
        await busy_lock.acquire()
        monkeypatch.setattr(dw, "_get_catalog_lock", lambda _key: busy_lock)
        monkeypatch.setattr(dw, "LOCK_BUSY_REQUEUE_DELAY", 0)

        def factory():
            db = AsyncMock()

            async def _execute(stmt):
                text = str(stmt)
                result = MagicMock()
                if "UPDATE data_fetch_jobs" in text:
                    events.append("update")
                    result.rowcount = 1
                else:
                    result.scalar_one_or_none = MagicMock(return_value=job)
                return result

            db.execute = AsyncMock(side_effect=_execute)
            return _FakeSessionCtx(db)

        monkeypatch.setattr(dw, "get_session_factory", lambda: factory)
        fake_rds = AsyncMock()
        monkeypatch.setattr(dw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        pipeline_constructed = []
        import tinohelm.data.pipeline as pkg_pipeline

        class _Boom(pkg_pipeline.BinanceVisionPipeline):
            def __init__(self, *a, **k):
                pipeline_constructed.append(True)

        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _Boom)

        try:
            await dw._process_job("job-lock-busy", "redis://x", "/cat")
        finally:
            busy_lock.release()

        assert "update" not in events
        assert pipeline_constructed == []
        # #164: the DB already owns scheduling — we must NOT push the job_id
        # back onto Redis. The row is still ``status='queued'``, so the next
        # drain pass will pick it up when the catalog lock clears.
        for call in fake_rds.lpush.await_args_list:
            assert call.args[1] != "job-lock-busy", (
                "lock-busy path must not re-enqueue a job_id under the new scheduler"
            )
        fake_rds.publish.assert_not_called()

    async def test_deferred_locked_job_waits_without_touching_redis(self, monkeypatch):
        # Under the DB-driven scheduler a lock-busy deferral is a pure
        # back-off: the row stays ``queued`` so the next drain naturally
        # retries. We only need a delay, nothing pushed onto Redis.
        fake_rds = AsyncMock()
        sleeps: list[float] = []

        async def fake_sleep(delay: float):
            sleeps.append(delay)

        monkeypatch.setattr(dw, "LOCK_BUSY_REQUEUE_DELAY", 0.75)
        monkeypatch.setattr(dw.asyncio, "sleep", fake_sleep)

        await dw._defer_locked_queued_job(fake_rds, "job-delay")

        assert sleeps == [0.75]
        for call in fake_rds.lpush.await_args_list:
            assert call.args[1] != "job-delay", (
                "job_id must never be written to Redis under the new scheduler"
            )

    async def test_preclaim_cancellation_requeues_queued_job(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        fake_rds = AsyncMock()
        monkeypatch.setattr(dw.aioredis, "from_url", lambda *_a, **_k: fake_rds)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")
        monkeypatch.setattr(dw, "_load_queued_job", AsyncMock(return_value=job))
        claim = AsyncMock()
        monkeypatch.setattr(dw, "_claim_queued_job", claim)

        acquire_started = asyncio.Event()

        async def acquire_never_finishes(_lock_key):
            acquire_started.set()
            await asyncio.sleep(10)

        monkeypatch.setattr(dw, "_try_acquire_catalog_lock", acquire_never_finishes)

        task = asyncio.create_task(dw._process_job("job-preclaim-cancel", "redis://x", "/cat"))
        await acquire_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # #164: pre-claim cancellation leaves the row queued; we rely on
        # the DB-driven drain to pick it up again rather than shoving the
        # job_id back onto a Redis list.
        for call in fake_rds.lpush.await_args_list:
            assert call.args[1] != "job-preclaim-cancel", (
                "pre-claim cancellation must not re-push the job_id onto Redis"
            )
        claim.assert_not_awaited()
        fake_rds.close.assert_awaited()

    async def test_preclaim_exception_marks_queued_job_failed(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        fake_rds = AsyncMock()
        monkeypatch.setattr(dw.aioredis, "from_url", lambda *_a, **_k: fake_rds)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")
        monkeypatch.setattr(dw, "_load_queued_job", AsyncMock(return_value=job))

        async def acquire_raises(_lock_key):
            raise RuntimeError("catalog lock unavailable")

        monkeypatch.setattr(dw, "_try_acquire_catalog_lock", acquire_raises)
        claim = AsyncMock()
        mark_failed = AsyncMock(return_value=True)
        monkeypatch.setattr(dw, "_claim_queued_job", claim)
        monkeypatch.setattr(dw, "_guarded_queued_failure_update", mark_failed)

        await dw._process_job("job-preclaim-error", "redis://x", "/cat")

        claim.assert_not_awaited()
        mark_failed.assert_awaited_once()
        values = mark_failed.await_args.args[2]
        assert values["status"] == "failed"
        assert "catalog lock unavailable" in values["error"]
        final = fake_rds.publish.await_args_list[-1]
        assert final.args[0] == "tino:data:events"
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.failed"
        assert payload["job_id"] == "job-preclaim-error"
        fake_rds.close.assert_awaited()

    async def test_claim_error_after_running_update_uses_running_failure_path(self, monkeypatch):
        job = SimpleNamespace(
            status="queued",
            symbol="BTCUSDT", data_type="klines", interval="1m",
            start_date="2025-01-01", end_date="2025-01-02", asset_class="futures",
        )
        fake_rds = AsyncMock()
        monkeypatch.setattr(dw.aioredis, "from_url", lambda *_a, **_k: fake_rds)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")
        monkeypatch.setattr(dw, "_load_queued_job", AsyncMock(return_value=job))
        lock = MagicMock()
        monkeypatch.setattr(dw, "_try_acquire_catalog_lock", AsyncMock(return_value=lock))
        monkeypatch.setattr(dw, "_claim_queued_job", AsyncMock(side_effect=RuntimeError("post-claim read failed")))
        monkeypatch.setattr(dw, "_job_is_still_running", AsyncMock(return_value=True))
        running_failed = AsyncMock(return_value=True)
        queued_failed = AsyncMock(return_value=True)
        monkeypatch.setattr(dw, "_guarded_terminal_update", running_failed)
        monkeypatch.setattr(dw, "_guarded_queued_failure_update", queued_failed)

        await dw._process_job("job-claim-error", "redis://x", "/cat")

        lock.release.assert_called_once()
        queued_failed.assert_not_awaited()
        running_failed.assert_awaited_once()
        values = running_failed.await_args.args[2]
        assert values["status"] == "failed"
        assert "post-claim read failed" in values["error"]
        fake_rds.lpush.assert_not_called()
        final = fake_rds.publish.await_args_list[-1]
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.failed"
        assert payload["job_id"] == "job-claim-error"
        fake_rds.close.assert_awaited()

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
                result.scalar_one_or_none = MagicMock(return_value=job)
                db.execute = AsyncMock(return_value=result)
            elif calls["count"] == 2:
                result = MagicMock()
                result.rowcount = 1
                db.execute = AsyncMock(return_value=result)
            elif calls["count"] == 3:
                result = MagicMock()
                result.scalar_one_or_none = MagicMock(return_value=job)
                db.execute = AsyncMock(return_value=result)
            else:
                async def _execute(stmt):
                    result = MagicMock()
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

    async def test_stop_and_wait_awaits_consumer_cancellation_cleanup(self, monkeypatch):
        cleanup_done: list[bool] = []

        async def _fake_consumer(*_a, **_k):
            try:
                await asyncio.sleep(5)
            finally:
                await asyncio.sleep(0.01)
                cleanup_done.append(True)

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)
        monkeypatch.setattr(
            dw,
            "get_settings",
            lambda: SimpleNamespace(data=SimpleNamespace(job_concurrency=1)),
            raising=False,
        )
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://x", catalog_path="/cat")
        await asyncio.sleep(0.01)
        await dw.stop_data_worker_and_wait(timeout=1.0)

        assert task.done() is True
        assert cleanup_done == [True]
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

    async def test_wake_token_triggers_drain(self, monkeypatch):
        """After #164 the consumer callback is a wake signal handler.

        Whatever value pops off Redis (we pass a dummy sentinel here), the
        worker must ignore it as a scheduling decision and instead call
        ``drain_once(redis_url=..., catalog_path=...)`` so the DB decides
        what actually runs next.
        """
        captured: dict = {}

        async def _fake_consumer(redis_url, queue_key, process_job, **_k):
            await process_job("any-token")  # content is irrelevant

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)

        async def _fake_drain(*, redis_url: str, catalog_path: str) -> int:
            captured["redis_url"] = redis_url
            captured["catalog_path"] = catalog_path
            return 0

        monkeypatch.setattr(dw, "drain_once", _fake_drain)
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://r", catalog_path="/c")
        await task

        assert captured == {"redis_url": "redis://r", "catalog_path": "/c"}
