"""Issue #166: legacy backlog adoption via ``batch_id`` backfill.

Pre-#163 ``DataFetchJob`` rows have ``batch_id = NULL`` because the column
didn't exist yet. The new scheduler still handles them (they count as
single-job FetchBatches via ``COALESCE(batch_id, job_id)`` in the
``ORDER BY`` subquery), but that loses the "these were all one submission"
hint — every legacy row becomes its own FetchBatch and strict cross-batch
FIFO breaks their original grouping.

PRD #162 decisions #23–#24 require a rule-based, deterministic backfill
that groups legacy rows by shared ``created_at`` (the closest proxy for
"same submission" we have after-the-fact). This module tests that rule.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tinohelm.data import worker as dw
from tinohelm.db.models import Base, DataFetchJob


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _job(
    *,
    batch_id: str | None,
    symbol: str,
    status: str = "queued",
    created: datetime,
    start: date = date(2026, 1, 1),
) -> DataFetchJob:
    return DataFetchJob(
        job_id=str(uuid.uuid4()),
        batch_id=batch_id,
        symbol=symbol,
        data_type="klines",
        interval="1m",
        start_date=start,
        end_date=start,
        asset_class="um",
        status=status,
        created_at=created,
    )


async def _seed(factory, rows):
    async with factory() as db:
        for r in rows:
            db.add(r)
        await db.commit()


async def _load_all(factory):
    async with factory() as db:
        return list((await db.execute(select(DataFetchJob))).scalars().all())


class TestBackfillLegacyBatchIds:
    async def test_rows_sharing_created_at_become_one_batch(self, factory):
        """Same fetch-batch submission ⇒ same server-side ``now()`` ⇒ same
        ``created_at``. Legacy rows with identical ``created_at`` must end
        up sharing one batch_id after backfill.
        """
        ts = datetime(2025, 6, 1, 10, 0, 0)
        rows = [
            _job(batch_id=None, symbol="BTCUSDT", created=ts),
            _job(batch_id=None, symbol="ETHUSDT", created=ts),
            _job(batch_id=None, symbol="SOLUSDT", created=ts),
        ]
        await _seed(factory, rows)

        touched = await dw.backfill_legacy_batch_ids(factory)

        assert touched == 3
        after = await _load_all(factory)
        batch_ids = {j.batch_id for j in after}
        assert None not in batch_ids, "all legacy rows must be backfilled"
        assert len(batch_ids) == 1, (
            f"rows with identical created_at must share one batch_id; got {batch_ids}"
        )

    async def test_distinct_created_at_groups_become_distinct_batches(self, factory):
        """Two separate submissions (distinct ``created_at``) must remain
        two distinct FetchBatches after backfill — we are reconstructing
        submission boundaries, not flattening all legacy rows."""
        t1 = datetime(2025, 6, 1, 10, 0, 0)
        t2 = datetime(2025, 6, 1, 10, 5, 0)
        rows = [
            _job(batch_id=None, symbol="BTCUSDT", created=t1),
            _job(batch_id=None, symbol="ETHUSDT", created=t1),
            _job(batch_id=None, symbol="ADAUSDT", created=t2),
            _job(batch_id=None, symbol="SOLUSDT", created=t2),
        ]
        await _seed(factory, rows)

        await dw.backfill_legacy_batch_ids(factory)

        after = {j.symbol: j.batch_id for j in await _load_all(factory)}
        assert after["BTCUSDT"] == after["ETHUSDT"]
        assert after["ADAUSDT"] == after["SOLUSDT"]
        assert after["BTCUSDT"] != after["ADAUSDT"], (
            "distinct created_at groups must stay in distinct batches"
        )

    async def test_existing_batch_ids_are_preserved(self, factory):
        """Rows that already carry a ``batch_id`` (post-#163 writes) must
        not be re-grouped by this migration."""
        ts = datetime(2025, 6, 1, 10, 0, 0)
        existing_batch = "real-batch-abc"
        rows = [
            _job(batch_id=existing_batch, symbol="BTCUSDT", created=ts),
            _job(batch_id=None, symbol="ETHUSDT", created=ts),
        ]
        await _seed(factory, rows)

        touched = await dw.backfill_legacy_batch_ids(factory)

        # Only the NULL row should be touched.
        assert touched == 1
        after = {j.symbol: j.batch_id for j in await _load_all(factory)}
        assert after["BTCUSDT"] == existing_batch
        assert after["ETHUSDT"] != existing_batch, (
            "legacy NULL row must get its OWN batch_id, not be merged into "
            "an unrelated real batch that happens to share created_at"
        )
        assert after["ETHUSDT"] is not None

    async def test_idempotent_second_call_is_noop(self, factory):
        """Running backfill twice must be safe — the second call has
        nothing to touch."""
        ts = datetime(2025, 6, 1, 10, 0, 0)
        await _seed(factory, [
            _job(batch_id=None, symbol="BTCUSDT", created=ts),
            _job(batch_id=None, symbol="ETHUSDT", created=ts),
        ])

        first = await dw.backfill_legacy_batch_ids(factory)
        second = await dw.backfill_legacy_batch_ids(factory)

        assert first == 2
        assert second == 0, "second pass must find nothing to backfill"

    async def test_empty_db_returns_zero(self, factory):
        assert await dw.backfill_legacy_batch_ids(factory) == 0

    async def test_lone_legacy_row_becomes_single_job_batch(self, factory):
        """PRD #162: "Treat backtest-triggered standalone fetches as
        single-job FetchBatch instances". A solitary legacy row has no
        siblings to share a batch with — it still must get a batch_id so
        the scheduler treats it as its own single-job batch, not as an
        unbounded NULL batch shared with future legacy arrivals."""
        ts = datetime(2025, 6, 1, 10, 0, 0)
        await _seed(factory, [
            _job(batch_id=None, symbol="BTCUSDT", created=ts),
        ])

        touched = await dw.backfill_legacy_batch_ids(factory)

        assert touched == 1
        rows = await _load_all(factory)
        assert rows[0].batch_id is not None
        assert isinstance(rows[0].batch_id, str)
        assert len(rows[0].batch_id) == 36  # UUID

    async def test_update_count_scales_with_groups_not_rows(self, factory):
        """Guard against a per-row UPDATE regression: the backfill must
        scale its DB round-trips with the number of distinct ``created_at``
        groups, not the number of legacy rows. A large fetch-batch
        submission writes hundreds of rows under one ``now()`` — a
        per-row UPDATE would hold row locks open for the full scan
        duration under heavy legacy backlogs."""
        # 50 rows across 2 submission groups (25 each).
        t1 = datetime(2025, 6, 1, 10, 0, 0)
        t2 = datetime(2025, 6, 1, 10, 5, 0)
        rows = []
        for idx in range(25):
            rows.append(_job(batch_id=None, symbol=f"SYM{idx}A", created=t1))
            rows.append(_job(batch_id=None, symbol=f"SYM{idx}B", created=t2))
        await _seed(factory, rows)

        update_stmts: list[str] = []

        class _ExecuteSpy:
            def __init__(self, inner_sessionmaker):
                self._inner_sessionmaker = inner_sessionmaker

            async def __aenter__(self):
                self._ctx = self._inner_sessionmaker()
                self._db = await self._ctx.__aenter__()
                real_execute = self._db.execute

                async def _spy_execute(stmt, *args, **kwargs):
                    if str(stmt).strip().upper().startswith("UPDATE"):
                        update_stmts.append(str(stmt))
                    return await real_execute(stmt, *args, **kwargs)

                self._db.execute = _spy_execute
                return self._db

            async def __aexit__(self, *exc):
                return await self._ctx.__aexit__(*exc)

        def spy_factory():
            return _ExecuteSpy(factory)

        touched = await dw.backfill_legacy_batch_ids(spy_factory)

        assert touched == 50
        # Exactly 2 UPDATEs — one per distinct ``created_at`` group.
        # A per-row UPDATE regression would emit 50.
        assert len(update_stmts) == 2, (
            f"expected 2 grouped UPDATEs, got {len(update_stmts)}; "
            "backfill must issue one UPDATE per distinct created_at"
        )
