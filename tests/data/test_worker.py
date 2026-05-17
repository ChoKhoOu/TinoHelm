"""Tests for ``tinohelm.data.worker`` — data-fetch queue worker."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

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
# _count_queued_jobs — cheap COUNT query, never load-then-len
# ---------------------------------------------------------------------------


class TestCountQueuedJobs:
    """The counter runs during startup recovery where backlog can be large;
    it must never load every queued row into Python just to size the list.
    """

    async def _build_factory(self):
        import uuid
        from datetime import date, datetime

        import pytest_asyncio  # noqa: F401  (ensures plugin is loaded)
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            async_sessionmaker,
            create_async_engine,
        )

        from tinohelm.db.models import Base, DataFetchJob

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def _seed(rows):
            async with factory() as db:
                for r in rows:
                    db.add(r)
                await db.commit()

        def _row(status: str):
            return DataFetchJob(
                job_id=str(uuid.uuid4()),
                batch_id=None,
                symbol="BTCUSDT",
                data_type="klines",
                interval="1m",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 1),
                asset_class="um",
                status=status,
                created_at=datetime(2026, 1, 1, 12, 0, 0),
            )

        return factory, _seed, _row, engine, DataFetchJob

    async def test_returns_zero_when_no_queued_rows(self):
        factory, _seed, _row, engine, model = await self._build_factory()
        try:
            # Seed a few non-queued rows to prove the filter works.
            await _seed([_row("running"), _row("completed"), _row("failed")])
            count = await dw._count_queued_jobs(factory, model)
            assert count == 0
        finally:
            await engine.dispose()

    async def test_counts_only_queued_rows(self):
        factory, _seed, _row, engine, model = await self._build_factory()
        try:
            await _seed([
                _row("queued"),
                _row("queued"),
                _row("queued"),
                _row("running"),
                _row("completed"),
                _row("failed"),
                _row("cancelled"),
            ])
            count = await dw._count_queued_jobs(factory, model)
            assert count == 3
        finally:
            await engine.dispose()

    async def test_uses_count_query_not_row_materialization(self, monkeypatch):
        """On large backlogs we must not pull every queued row into Python to
        ``len()`` them. The underlying statement must be a COUNT aggregation
        so the DB returns a single scalar — emitting one row's worth of
        traffic instead of scaling with the queue depth.
        """
        from tinohelm.db.models import DataFetchJob

        captured_stmts: list[str] = []

        def factory():
            db = AsyncMock()

            async def _execute(stmt):
                captured_stmts.append(str(stmt))
                result = MagicMock()
                # COUNT path: scalar_one() returns the aggregated count.
                result.scalar_one = MagicMock(return_value=7)
                # Legacy row-materialize path would instead call
                # ``.scalars().all()`` and ``len()`` the list; wire a large
                # value so a regression is obviously wrong, not silent.
                scalars = MagicMock()
                scalars.all = MagicMock(return_value=["leak"] * 9999)
                result.scalars = MagicMock(return_value=scalars)
                return result

            db.execute = AsyncMock(side_effect=_execute)
            return _FakeSessionCtx(db)

        count = await dw._count_queued_jobs(factory, DataFetchJob)

        assert count == 7, (
            "impl must read the scalar count; a materialize-then-len bug "
            "would return 9999 here"
        )
        assert captured_stmts, "expected exactly one COUNT query to be executed"
        first = captured_stmts[0].upper()
        assert "COUNT" in first, (
            f"must use a SQL aggregate; got: {captured_stmts[0]!r}"
        )


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

    def test_next_job_subquery_reuses_same_bind_for_interval_coalesce_on_postgres(self):
        """PostgreSQL requires SELECT and GROUP BY to reference the same
        expression tree. If SQLAlchemy emits two distinct bind params for the
        ``coalesce(interval, '')`` fallback, PG rejects the query with
        ``GroupingError`` even though the runtime values are equal.
        """
        compiled = dw._next_queued_job_id_subquery().compile(
            dialect=postgresql.dialect()
        )
        sql = str(compiled)

        assert "coalesce(data_fetch_jobs.interval, %(empty_interval)s)" in sql
        assert compiled.params.get("empty_interval") == ""
        assert "coalesce(data_fetch_jobs.interval, %(coalesce_" not in sql

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
            started_at=datetime(2025, 1, 1, 12, 0, 0),
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
        assert got.started_at is not None
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

    async def test_returns_skip_sentinel_when_row_cancelled_between_claim_and_load(self, monkeypatch):
        """Cancellation can land after the atomic UPDATE but before the
        follow-up SELECT loads the row. The worker must surface
        ``CLAIM_SKIP`` — not ``None`` — so the caller can tell "one row
        raced with cancellation" apart from "queue is empty".

        Returning ``None`` here would make ``drain_once`` exit early and
        strand every sibling queued row behind this one until the next
        wake signal (which ``cancel_data_fetch_job`` does not push).
        """
        picked_job_id = "cancelled-between"
        cancelled_job = SimpleNamespace(
            job_id=picked_job_id,
            status=dw.STATUS_CANCELLED,
            symbol="BTCUSDT",
            data_type="klines",
            interval="1m",
            start_date="2025-01-01",
            end_date="2025-01-02",
            asset_class="um",
        )

        def factory():
            db = AsyncMock()

            async def _execute(stmt):
                result = MagicMock()
                if "UPDATE" in str(stmt).upper():
                    # Atomic claim succeeded — row flipped queued → running.
                    result.rowcount = 1
                    result.scalar_one_or_none = MagicMock(return_value=picked_job_id)
                else:
                    # Between the two sessions another actor flipped the
                    # row to cancelled (e.g. the cancel API route).
                    result.scalar_one_or_none = MagicMock(return_value=cancelled_job)
                return result

            db.execute = AsyncMock(side_effect=_execute)
            return _FakeSessionCtx(db)

        assert await dw.claim_next_queued_job(factory) is dw.CLAIM_SKIP

    async def test_returns_skip_sentinel_when_row_missing_after_claim(self, monkeypatch):
        """Same concern as the cancellation race but on a different
        pathway: the follow-up SELECT may find the row has been deleted
        (rare, but possible if a concurrent admin wipe lands between the
        two sessions). Surface ``CLAIM_SKIP`` so the drain keeps going
        rather than mistaking a single missing row for queue exhaustion.
        """
        picked_job_id = "vanished-between"

        def factory():
            db = AsyncMock()

            async def _execute(stmt):
                result = MagicMock()
                if "UPDATE" in str(stmt).upper():
                    result.rowcount = 1
                    result.scalar_one_or_none = MagicMock(return_value=picked_job_id)
                else:
                    # Row was deleted between the UPDATE commit and the SELECT.
                    result.scalar_one_or_none = MagicMock(return_value=None)
                return result

            db.execute = AsyncMock(side_effect=_execute)
            return _FakeSessionCtx(db)

        assert await dw.claim_next_queued_job(factory) is dw.CLAIM_SKIP


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

    async def test_stops_when_process_returns_false_without_reclaiming(self, monkeypatch):
        """When ``_process_claimed_job`` returns ``False`` (catalog lock busy,
        row reverted to queued), ``drain_once`` must stop this tick instead
        of re-claiming the same row in a tight loop.

        Without this guard two consumers contending on the same
        ``(symbol, data_type, interval)`` bucket could hot-spin: each claim
        would pick the reverted row, fail the lock acquisition, revert, and
        immediately re-claim — burning CPU until the other consumer releases.
        """
        claim_calls = 0
        busy_job = SimpleNamespace(
            job_id="lock-busy", symbol="BTC", data_type="klines", interval="1m",
        )

        async def fake_claim(_factory):
            nonlocal claim_calls
            claim_calls += 1
            return busy_job

        process_calls: list[str] = []

        async def fake_process(job, redis_url: str, catalog_path: str) -> bool:
            process_calls.append(job.job_id)
            return False  # row was reverted queued → catalog lock busy

        monkeypatch.setattr(dw, "claim_next_queued_job", fake_claim)
        monkeypatch.setattr(dw, "_process_claimed_job", fake_process)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")

        n = await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        # One row was claimed and deferred; drain must exit without looping.
        assert n == 1
        assert claim_calls == 1, "lock-busy return must terminate the drain"
        assert process_calls == ["lock-busy"]

    async def test_skip_sentinel_does_not_halt_drain(self, monkeypatch):
        """A mid-claim cancellation (UPDATE commits, but the follow-up SELECT
        sees ``status='cancelled'``) must not be conflated with "queue empty".

        ``claim_next_queued_job`` signals this via the ``CLAIM_SKIP`` sentinel
        so ``drain_once`` continues with the next runnable row. Without this,
        a single cancelled row would strand every sibling behind it until the
        next wake token — and ``_requeue_queued_job_best_effort`` /
        ``cancel_data_fetch_job`` don't currently push one.
        """
        good = SimpleNamespace(
            job_id="good", symbol="ETH", data_type="klines", interval="1m",
        )
        # Sequence: first claim picks a row that raced with cancellation
        # (CLAIM_SKIP), then the next claim produces a healthy row, then
        # the queue drains.
        outcomes: list = [dw.CLAIM_SKIP, good, None]

        async def fake_claim(_factory):
            return outcomes.pop(0)

        processed: list[str] = []

        async def fake_process(job, redis_url: str, catalog_path: str) -> bool:
            processed.append(job.job_id)
            return True

        monkeypatch.setattr(dw, "claim_next_queued_job", fake_claim)
        monkeypatch.setattr(dw, "_process_claimed_job", fake_process)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")

        n = await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        # The skip is not counted as "processed", but the drain kept going
        # and picked up the runnable sibling.
        assert processed == ["good"]
        assert n == 1
        assert outcomes == [], "drain must exhaust outcomes, not stop on skip"

    async def test_schedules_delayed_wake_on_lock_busy_in_single_consumer_mode(
        self, monkeypatch
    ):
        """Single-consumer livelock guard: when the only consumer defers a
        lock-busy row, nobody else is around to push the next WAKE_TOKEN.
        ``drain_once`` must hand off to ``_schedule_delayed_wake`` so the
        queued row is retried after ``LOCK_BUSY_REQUEUE_DELAY`` — otherwise
        the job stalls indefinitely.
        """
        busy_job = SimpleNamespace(
            job_id="lock-busy", symbol="BTC", data_type="klines", interval="1m",
        )

        async def fake_claim(_factory):
            return busy_job

        async def fake_process(job, redis_url: str, catalog_path: str) -> bool:
            return False  # row reverted to queued

        scheduled_urls: list[str] = []

        async def fake_wake(redis_url: str) -> None:
            scheduled_urls.append(redis_url)

        settings_stub = SimpleNamespace(data=SimpleNamespace(job_concurrency=1))

        monkeypatch.setattr(dw, "claim_next_queued_job", fake_claim)
        monkeypatch.setattr(dw, "_process_claimed_job", fake_process)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")
        monkeypatch.setattr(dw, "get_settings", lambda: settings_stub)
        monkeypatch.setattr(dw, "_schedule_delayed_wake", fake_wake)

        await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        # Let the scheduled self-wake task run.
        await asyncio.sleep(0)

        assert scheduled_urls == ["redis://x"], (
            "single-consumer lock-busy must schedule a delayed self-wake"
        )

    async def test_skips_delayed_wake_when_multiple_consumers(self, monkeypatch):
        """With ``job_concurrency > 1`` the sibling consumer will re-drain
        once it finishes its current row — scheduling an extra wake would
        only spam the queue. The livelock path must stay single-consumer.
        """
        busy_job = SimpleNamespace(
            job_id="lock-busy", symbol="BTC", data_type="klines", interval="1m",
        )

        async def fake_claim(_factory):
            return busy_job

        async def fake_process(job, redis_url: str, catalog_path: str) -> bool:
            return False

        scheduled: list[str] = []

        async def fake_wake(redis_url: str) -> None:
            scheduled.append(redis_url)

        settings_stub = SimpleNamespace(data=SimpleNamespace(job_concurrency=2))

        monkeypatch.setattr(dw, "claim_next_queued_job", fake_claim)
        monkeypatch.setattr(dw, "_process_claimed_job", fake_process)
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")
        monkeypatch.setattr(dw, "get_settings", lambda: settings_stub)
        monkeypatch.setattr(dw, "_schedule_delayed_wake", fake_wake)

        await dw.drain_once(redis_url="redis://x", catalog_path="/cat")
        await asyncio.sleep(0)

        assert scheduled == [], "multi-consumer mode must not self-wake"


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
        rds = AsyncMock()
        count = await dw.recover_interrupted_jobs(rds)

        assert count == 4
        assert flipped == {"factory": factory_marker, "model": dw.DataFetchJob}

    async def test_pushes_one_wake_token_when_queued_work_exists(self, monkeypatch):
        """With queued rows still present, recovery must wake exactly ONE
        consumer — not one wake token per job (PRD #162 decision #20)."""
        monkeypatch.setattr(dw, "_flip_running_to_queued", AsyncMock(return_value=3))
        monkeypatch.setattr(dw, "_count_queued_jobs", AsyncMock(return_value=12))
        monkeypatch.setattr(dw, "get_session_factory", lambda: "factory")

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

        rds = AsyncMock()
        await dw.recover_interrupted_jobs(rds)

        rds.lpush.assert_not_awaited()



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


def _fake_pipeline_result(objects_count: int = 42, partial: bool = False, last_available_date=None) -> SimpleNamespace:
    return SimpleNamespace(objects_count=objects_count, partial=partial, last_available_date=last_available_date)


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

    async def test_orphan_sweeper_uses_started_at_not_created_at(self, monkeypatch):
        from datetime import datetime
        from types import SimpleNamespace

        captured = {}

        class _Result:
            def all(self):
                return []

        class _DB:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, stmt):
                captured["stmt"] = str(stmt)
                return _Result()

            async def commit(self):
                captured["committed"] = True

        monkeypatch.setattr(dw, "get_session_factory", lambda: lambda: _DB())
        monkeypatch.setattr(dw, "datetime", SimpleNamespace(now=lambda _tz=None: datetime(2026, 1, 1, 12, 0, 0)))
        monkeypatch.setattr(dw, "_ORPHAN_RUNNING_THRESHOLD", 600)

        await dw._sweep_orphan_running_jobs()

        assert captured.get("committed") is True
        assert "started_at" in captured["stmt"]
        assert "created_at" not in captured["stmt"]

    async def test_orphan_sweeper_publishes_failed_events(self, monkeypatch):
        from datetime import datetime
        from types import SimpleNamespace

        fake_rds = AsyncMock()
        fake_rds.close = AsyncMock()

        class _Result:
            def all(self):
                return [SimpleNamespace(job_id="job-1", symbol="BTCUSDT", data_type="klines")]

        class _DB:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, _stmt):
                return _Result()

            async def commit(self):
                return None

        monkeypatch.setattr(dw, "get_session_factory", lambda: lambda: _DB())
        monkeypatch.setattr(dw.aioredis, "from_url", lambda *_a, **_k: fake_rds)
        monkeypatch.setattr(dw, "get_settings", lambda: SimpleNamespace(redis=SimpleNamespace(url="redis://x")))
        monkeypatch.setattr(dw, "_utcnow_naive", lambda: datetime(2026, 1, 1, 12, 10, 0))

        count = await dw._sweep_orphan_running_jobs()

        assert count == 1
        final = fake_rds.publish.await_args_list[-1]
        assert final.args[0] == "tino:data:events"
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.failed"
        assert payload["job_id"] == "job-1"
        fake_rds.close.assert_awaited_once()

    async def test_cancelled_claimed_job_publishes_failed_event(self, monkeypatch):
        job = SimpleNamespace(
            job_id="job-cancelled-running",
            symbol="BTCUSDT",
            data_type="klines",
            interval="1m",
            start_date="2025-01-01",
            end_date="2025-01-02",
            asset_class="um",
        )

        fake_rds = AsyncMock()
        fake_rds.close = AsyncMock()
        monkeypatch.setattr(dw.aioredis, "from_url", lambda *_a, **_k: fake_rds)

        class _DB:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, _stmt):
                result = MagicMock()
                result.rowcount = 1
                return result

            async def commit(self):
                return None

        monkeypatch.setattr(dw, "get_session_factory", lambda: lambda: _DB())

        import tinohelm.data.pipeline as pkg_pipeline

        class _CancellingPipeline:
            def __init__(self, *a, **k):
                pass

            async def ingest(self, **_k):
                raise asyncio.CancelledError

        monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _CancellingPipeline)

        with pytest.raises(asyncio.CancelledError):
            await dw._process_claimed_job(job, "redis://x", "/cat")

        final = fake_rds.publish.await_args_list[-1]
        assert final.args[0] == "tino:data:events"
        payload = json.loads(final.args[1])
        assert payload["type"] == "data.fetch.failed"
        assert payload["job_id"] == "job-cancelled-running"
        assert payload["error"] == "Cancelled during execution"

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
            except asyncio.CancelledError:
                cleanup_done.append(True)
                raise

        monkeypatch.setattr(dw, "consumer_loop", _fake_consumer)
        monkeypatch.setattr(
            dw,
            "get_settings",
            lambda: SimpleNamespace(data=SimpleNamespace(job_concurrency=1)),
            raising=False,
        )
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://x", catalog_path="/cat")
        await asyncio.sleep(0.05)
        await dw.stop_data_worker_and_wait(timeout=2.0)

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

        async def _noop_sweep():
            return

        monkeypatch.setattr(dw, "_orphan_sweep_loop", _noop_sweep)
        dw._handle.stop()

        task = dw.start_data_worker(redis_url="redis://r", catalog_path="/c")
        await asyncio.wait_for(task, timeout=5.0)

        assert captured == {"redis_url": "redis://r", "catalog_path": "/c"}
