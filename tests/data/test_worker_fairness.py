"""Issue #165: FetchBucket fairness inside one FetchBatch.

``claim_next_queued_job(factory)`` must pick the next runnable
``DataFetchJob`` using "least-started FetchBucket first", where a
FetchBucket is the ``(symbol, data_type, interval)`` tuple and
started_count is ``running + completed + failed + cancelled``.

These tests drive a real in-memory SQLite session: the acceptance
criteria from #165 live inside the ORDER BY of one SQL statement
(see ADR 0001 note for slice #165), so we verify externally-visible
claim behavior against real rows rather than mocked UPDATE text.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tinohelm.data import worker as dw
from tinohelm.db.models import Base, DataFetchJob


@pytest_asyncio.fixture
async def factory():
    """Fresh in-memory SQLite + ORM schema per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _job(
    *,
    batch_id: str,
    symbol: str,
    data_type: str = "klines",
    interval: str | None = "1m",
    status: str = "queued",
    start: date,
    created: datetime,
) -> DataFetchJob:
    return DataFetchJob(
        job_id=str(uuid.uuid4()),
        batch_id=batch_id,
        symbol=symbol,
        data_type=data_type,
        interval=interval,
        start_date=start,
        end_date=start,
        asset_class="um",
        status=status,
        created_at=created,
    )


async def _seed(factory, rows: list[DataFetchJob]) -> None:
    async with factory() as db:
        for row in rows:
            db.add(row)
        await db.commit()


async def _job_by_id(factory, job_id: str) -> DataFetchJob | None:
    from sqlalchemy import select

    async with factory() as db:
        return (
            await db.execute(select(DataFetchJob).where(DataFetchJob.job_id == job_id))
        ).scalar_one_or_none()


class TestFetchBucketFairness:
    """Fairness: least-started bucket inside a batch wins the next claim."""

    async def test_bucket_with_running_job_yields_to_idle_bucket(self, factory):
        """Inside one batch, a bucket with an in-flight job must not starve a
        sibling bucket whose jobs have not started yet."""
        batch = "batch-A"
        base = datetime(2026, 5, 1, 12, 0, 0)
        hot = _job(
            batch_id=batch,
            symbol="BTCUSDT",
            status="running",  # already started — started_count = 1
            start=date(2026, 1, 1),
            created=base,
        )
        hot_queued = _job(
            batch_id=batch,
            symbol="BTCUSDT",
            start=date(2026, 1, 2),
            created=base + timedelta(seconds=1),
        )
        cold = _job(
            batch_id=batch,
            symbol="ETHUSDT",
            start=date(2026, 1, 3),
            created=base + timedelta(seconds=2),
        )
        await _seed(factory, [hot, hot_queued, cold])

        claimed = await dw.claim_next_queued_job(factory)

        assert claimed is not None
        # BTC bucket already has 1 running; ETH bucket has 0.
        # Fair scheduling must pick ETH even though BTC's queued row is older.
        assert claimed.symbol == "ETHUSDT", (
            f"expected ETH (idle bucket) to win; got {claimed.symbol} "
            f"(BTC bucket already running)"
        )

    async def test_same_bucket_picks_earliest_start_date(self, factory):
        """Inside one FetchBucket, ingest order must remain chronological so
        catalog consistency is preserved (#165 AC)."""
        batch = "batch-B"
        base = datetime(2026, 5, 2, 9, 0, 0)
        # All three rows sit in the SAME bucket (BTCUSDT / klines / 1m).
        # Insert them out of chronological order to prove the scheduler picks
        # by start_date, not by insertion / created_at.
        newest_start = _job(
            batch_id=batch,
            symbol="BTCUSDT",
            start=date(2026, 1, 10),
            created=base,  # oldest created_at
        )
        middle_start = _job(
            batch_id=batch,
            symbol="BTCUSDT",
            start=date(2026, 1, 5),
            created=base + timedelta(seconds=1),
        )
        earliest_start = _job(
            batch_id=batch,
            symbol="BTCUSDT",
            start=date(2026, 1, 1),
            created=base + timedelta(seconds=2),  # newest created_at
        )
        await _seed(factory, [newest_start, middle_start, earliest_start])

        claimed = await dw.claim_next_queued_job(factory)

        assert claimed is not None
        assert claimed.job_id == earliest_start.job_id, (
            "within one FetchBucket the claim order must be chronological "
            "by start_date; got start_date=%s" % claimed.start_date
        )

    @pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
    async def test_terminal_rows_count_toward_bucket_started_count(
        self, factory, terminal_status
    ):
        """Per PRD #162: bucket started_count = running + completed + failed +
        cancelled. A failed job must not let the same bucket jump the queue
        again — otherwise a bad symbol can still monopolize a worker slot by
        failing fast."""
        batch = "batch-C"
        base = datetime(2026, 5, 3, 14, 0, 0)
        already = _job(
            batch_id=batch,
            symbol="BTCUSDT",
            status=terminal_status,  # terminal — still counts as "started"
            start=date(2026, 1, 1),
            created=base,
        )
        btc_next = _job(
            batch_id=batch,
            symbol="BTCUSDT",
            start=date(2026, 1, 2),
            created=base + timedelta(seconds=1),
        )
        eth_first = _job(
            batch_id=batch,
            symbol="ETHUSDT",
            start=date(2026, 1, 3),
            created=base + timedelta(seconds=2),
        )
        await _seed(factory, [already, btc_next, eth_first])

        claimed = await dw.claim_next_queued_job(factory)

        assert claimed is not None
        assert claimed.symbol == "ETHUSDT", (
            f"terminal status {terminal_status!r} must still count toward "
            f"bucket started_count — ETH (untouched) should win over BTC"
        )

    async def test_older_batch_with_idle_bucket_wins_over_newer(self, factory):
        """Cross-batch FIFO holds when the older batch still has *idle*
        capacity — #166 soft FIFO only relaxes FIFO when the older batch
        cannot occupy the worker slot itself.

        Here the older batch has an untouched bucket (ETH, started_count=0)
        and the newer batch also has an untouched bucket. Because older's
        idle bucket can still make progress, it must win FIFO.
        """
        older_batch = "batch-older"
        newer_batch = "batch-newer"
        older_created = datetime(2026, 5, 4, 10, 0, 0)
        newer_created = datetime(2026, 5, 4, 11, 0, 0)

        older_running = _job(
            batch_id=older_batch,
            symbol="BTCUSDT",
            status="running",
            start=date(2026, 1, 1),
            created=older_created,
        )
        older_idle_queued = _job(
            batch_id=older_batch,
            symbol="SOLUSDT",  # different bucket → idle inside older
            start=date(2026, 1, 2),
            created=older_created + timedelta(seconds=1),
        )
        newer_idle_bucket = _job(
            batch_id=newer_batch,
            symbol="ETHUSDT",
            start=date(2026, 1, 5),
            created=newer_created,
        )
        await _seed(factory, [older_running, older_idle_queued, newer_idle_bucket])

        claimed = await dw.claim_next_queued_job(factory)

        assert claimed is not None
        assert claimed.batch_id == older_batch, (
            "older batch still has an idle bucket — FIFO must keep the claim "
            f"inside it under soft FIFO (got batch={claimed.batch_id})"
        )


class TestSoftFIFOAcrossBatches:
    """Issue #166: cross-batch soft FIFO.

    Older batches keep priority while they can still occupy a worker slot.
    Once every queued row of the older batch sits in a bucket that already
    has a running job (the older batch cannot saturate further concurrency
    this tick), a newer batch's idle bucket must win the claim — otherwise
    the worker sleeps on a catalog-locked bucket while unrelated work waits.
    """

    async def test_older_batch_saturated_yields_slot_to_newer_idle_bucket(
        self, factory
    ):
        """Soft FIFO *break* point.

        Setup:
          - older batch has ONE bucket; a job of it is already ``running``
            (bucket started_count=1) and one more ``queued`` row waits in
            the same bucket. The catalog lock for that bucket is busy, so
            picking the older queued row would just defer.
          - newer batch has an untouched bucket (started_count=0) with one
            ``queued`` row.

        Expected: the newer batch's idle bucket wins — strict FIFO would
        starve available worker capacity.
        """
        older_batch = "batch-older"
        newer_batch = "batch-newer"
        older_created = datetime(2026, 5, 7, 10, 0, 0)
        newer_created = datetime(2026, 5, 7, 11, 0, 0)

        older_running = _job(
            batch_id=older_batch,
            symbol="BTCUSDT",
            status="running",
            start=date(2026, 1, 1),
            created=older_created,
        )
        older_same_bucket_queued = _job(
            batch_id=older_batch,
            symbol="BTCUSDT",  # same bucket → started_count already 1
            start=date(2026, 1, 2),
            created=older_created + timedelta(seconds=1),
        )
        newer_idle = _job(
            batch_id=newer_batch,
            symbol="ETHUSDT",  # different bucket, started_count=0
            start=date(2026, 1, 5),
            created=newer_created,
        )
        await _seed(
            factory, [older_running, older_same_bucket_queued, newer_idle]
        )

        claimed = await dw.claim_next_queued_job(factory)

        assert claimed is not None
        assert claimed.batch_id == newer_batch, (
            "soft FIFO: older batch cannot saturate this slot (its only "
            "queued row sits in a bucket that is already running), so the "
            f"newer batch's idle bucket must win (got batch={claimed.batch_id})"
        )
        assert claimed.symbol == "ETHUSDT"

    async def test_legacy_null_batch_participates_in_soft_fifo(self, factory):
        """A legacy pre-#163 row (batch_id IS NULL) is still a single-job
        FetchBatch. If a newer real batch is fully saturated while a legacy
        row sits in an idle bucket, the legacy row must fill the worker
        slot — soft FIFO must not accidentally exclude NULL batch_id rows.
        """
        newer_batch = "batch-newer-real"
        legacy_created = datetime(2026, 5, 8, 9, 0, 0)
        newer_created = datetime(2026, 5, 8, 9, 0, 10)  # newer batch

        # Older "legacy" row: batch_id=None, idle bucket.
        legacy_idle = _job(
            batch_id=None,
            symbol="ADAUSDT",
            start=date(2026, 1, 1),
            created=legacy_created,
        )
        # Newer real batch: one running, one queued — same bucket (saturated).
        newer_running = _job(
            batch_id=newer_batch,
            symbol="BTCUSDT",
            status="running",
            start=date(2026, 1, 1),
            created=newer_created,
        )
        newer_queued_same_bucket = _job(
            batch_id=newer_batch,
            symbol="BTCUSDT",
            start=date(2026, 1, 2),
            created=newer_created + timedelta(seconds=1),
        )
        await _seed(
            factory,
            [legacy_idle, newer_running, newer_queued_same_bucket],
        )

        claimed = await dw.claim_next_queued_job(factory)

        assert claimed is not None
        # Legacy row is older by created_at AND its bucket is idle, so it
        # must win under either strict or soft FIFO.
        assert claimed.symbol == "ADAUSDT", (
            "legacy NULL batch_id row acts as its own single-job FetchBatch "
            "and must still participate in FIFO + fairness"
        )

    async def test_failure_in_one_bucket_does_not_block_sibling_buckets(
        self, factory
    ):
        """Best-effort batch semantics (PRD #162 decision #11 / #165 AC).

        Sequence:
          1. Batch has two buckets, each with one queued job.
          2. First claim lands on bucket A. Simulate pipeline failure by
             flipping the row to ``status='failed'``.
          3. Next claim must pick bucket B — the failure must NOT block
             sibling buckets from advancing.
        """
        from sqlalchemy import update as _update

        batch = "batch-D"
        base = datetime(2026, 5, 5, 9, 0, 0)
        a = _job(
            batch_id=batch,
            symbol="AAAUSDT",  # wins tiebreaker on symbol ASC
            start=date(2026, 1, 1),
            created=base,
        )
        b = _job(
            batch_id=batch,
            symbol="BBBUSDT",
            start=date(2026, 1, 1),
            created=base + timedelta(seconds=1),
        )
        await _seed(factory, [a, b])

        first = await dw.claim_next_queued_job(factory)
        assert first is not None
        assert first.symbol == "AAAUSDT", (
            f"symbol tiebreaker ascending expected AAA first; got {first.symbol}"
        )

        # Simulate pipeline failure on bucket A's job.
        async with factory() as db:
            await db.execute(
                _update(DataFetchJob)
                .where(DataFetchJob.job_id == first.job_id)
                .values(status="failed")
            )
            await db.commit()

        # Sibling bucket B must still advance — failure must not starve it.
        second = await dw.claim_next_queued_job(factory)
        assert second is not None, "bucket B must still be claimable after A fails"
        assert second.symbol == "BBBUSDT", (
            f"failure in bucket A must not block bucket B; got {second.symbol}"
        )

    async def test_drain_round_robins_across_buckets(self, factory, monkeypatch):
        """End-to-end: ``drain_once`` must advance across buckets round-robin
        rather than sequentially draining one bucket first.

        Given a single batch with two buckets and two queued jobs each, the
        first four claims (interleaved with "mark previous running→running
        stays" — simulating in-flight work) should alternate bucket symbols.
        """
        from sqlalchemy import update as _update

        batch = "batch-E"
        base = datetime(2026, 5, 6, 9, 0, 0)
        rows = [
            _job(batch_id=batch, symbol="AAAUSDT",
                 start=date(2026, 1, 1), created=base),
            _job(batch_id=batch, symbol="AAAUSDT",
                 start=date(2026, 1, 2), created=base + timedelta(seconds=1)),
            _job(batch_id=batch, symbol="BBBUSDT",
                 start=date(2026, 1, 1), created=base + timedelta(seconds=2)),
            _job(batch_id=batch, symbol="BBBUSDT",
                 start=date(2026, 1, 2), created=base + timedelta(seconds=3)),
        ]
        await _seed(factory, rows)

        # claim_next_queued_job flips to running; we keep that state so the
        # fairness formula reacts to actual in-flight counts.
        claims: list[str] = []
        for _ in range(4):
            got = await dw.claim_next_queued_job(factory)
            assert got is not None
            claims.append(got.symbol)

        # Round-robin inside the batch — AAA/BBB/AAA/BBB, not AAA/AAA/BBB/BBB.
        assert claims == ["AAAUSDT", "BBBUSDT", "AAAUSDT", "BBBUSDT"], (
            f"expected round-robin across FetchBuckets, got {claims}"
        )
