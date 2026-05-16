"""End-to-end drain integration tests.

The pre-#166 unit tests mock ``get_session_factory`` and the pipeline, so
they never exercise the full ``claim → execute → terminal`` chain. This
module uses a real in-memory SQLite session + a stub pipeline to assert
that after ``drain_once`` returns, queued rows actually reach the
pipeline, flip to ``completed``, and publish the completion event.

Why this matters: ``claim_next_queued_job`` atomically flips a row to
``running``. If the downstream executor re-filters on ``status='queued'``,
claimed rows are silently skipped and the backlog stalls at ``running``
forever — exactly the regression #164/#165/#166 were supposed to fix.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tinohelm.data import worker as dw
from tinohelm.db.models import Base, DataFetchJob


@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(dw, "get_session_factory", lambda: f)
    yield f
    await engine.dispose()


@pytest_asyncio.fixture
def fake_redis(monkeypatch):
    rds = AsyncMock()
    rds.close = AsyncMock()
    monkeypatch.setattr(dw.aioredis, "from_url", lambda *a, **k: rds)
    return rds


@pytest_asyncio.fixture
def stub_pipeline(monkeypatch):
    """Install a pipeline stub that records every ingest call."""
    import tinohelm.data.pipeline as pkg_pipeline

    calls: list[dict] = []

    class _Stub:
        def __init__(self, *a, **k):
            pass

        async def ingest(self, *, progress_cb, **kwargs):
            calls.append(kwargs)
            await progress_cb(100, "done")
            return SimpleNamespace(objects_count=1, partial=False, last_available_date=None)

    monkeypatch.setattr(pkg_pipeline, "BinanceVisionPipeline", _Stub)
    return calls


def _queued(symbol: str, start: date, created: datetime, batch: str = "b") -> DataFetchJob:
    return DataFetchJob(
        job_id=str(uuid.uuid4()),
        batch_id=batch,
        symbol=symbol,
        data_type="klines",
        interval="1m",
        start_date=start,
        end_date=start,
        asset_class="um",
        status="queued",
        created_at=created,
    )


async def _seed(factory, rows):
    async with factory() as db:
        for r in rows:
            db.add(r)
        await db.commit()


async def _all_rows(factory):
    async with factory() as db:
        return list((await db.execute(select(DataFetchJob))).scalars().all())


class TestDrainExecutesClaimedRows:
    """Tracer: a single queued row must go all the way to ``completed``."""

    async def test_claimed_row_with_busy_catalog_lock_reverts_to_queued(
        self, factory, fake_redis, stub_pipeline, monkeypatch
    ):
        """If the catalog lock is already held when ``drain_once`` claims a
        row, the executor must revert the row to ``queued`` rather than
        leave it stranded in ``running``. Otherwise a consumer that loses
        the lock race permanently parks a pre-claimed row.
        """
        import asyncio as _aio

        # Claim-time already flipped one row to running; now stage a busy
        # catalog lock so the executor cannot acquire it.
        busy = _aio.Lock()
        await busy.acquire()
        monkeypatch.setattr(dw, "_get_catalog_lock", lambda _key: busy)
        monkeypatch.setattr(dw, "LOCK_BUSY_REQUEUE_DELAY", 0)

        await _seed(factory, [
            _queued("BTCUSDT", date(2026, 1, 1), datetime(2026, 1, 1)),
        ])

        try:
            await dw.drain_once(redis_url="redis://x", catalog_path="/cat")
        finally:
            busy.release()

        rows = await _all_rows(factory)
        assert len(rows) == 1
        assert rows[0].status == "queued", (
            "a lock-busy pre-claimed row must be reverted to queued so the "
            f"next drain can re-claim it; found status={rows[0].status!r}"
        )
        # Pipeline should NOT have run — we never acquired the lock.
        assert stub_pipeline == []

    async def test_single_queued_row_reaches_completed(
        self, factory, fake_redis, stub_pipeline
    ):
        await _seed(factory, [
            _queued("BTCUSDT", date(2026, 1, 1), datetime(2026, 1, 1)),
        ])

        n = await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        assert n == 1, "drain should report one processed row"
        assert len(stub_pipeline) == 1, (
            "claimed row must reach pipeline.ingest — if 0, the claim→execute "
            "handoff dropped the row silently (see #164 ADR 0001)"
        )
        rows = await _all_rows(factory)
        assert len(rows) == 1
        assert rows[0].status == "completed"
        assert rows[0].progress == 100

        # Completion event published to tino:data:events.
        events = [
            json.loads(c.args[1])
            for c in fake_redis.publish.await_args_list
            if c.args[0] == "tino:data:events"
        ]
        assert events, "expected a tino:data:events publish on completion"
        assert events[-1]["type"] == "data.fetch.completed"

    async def test_round_robin_drain_reaches_terminal_for_all_rows(
        self, factory, fake_redis, stub_pipeline
    ):
        """End-to-end: one batch with two buckets × two queued jobs each
        (4 rows) must all reach ``completed`` after one ``drain_once``,
        and the execution order must alternate buckets (ADR 0002 fairness
        delivered through the real pipeline, not just the claim layer).
        """
        from datetime import timedelta

        base = datetime(2026, 5, 6, 9, 0, 0)
        rows = [
            _queued("AAAUSDT", date(2026, 1, 1), base, batch="e"),
            _queued("AAAUSDT", date(2026, 1, 2), base + timedelta(seconds=1), batch="e"),
            _queued("BBBUSDT", date(2026, 1, 1), base + timedelta(seconds=2), batch="e"),
            _queued("BBBUSDT", date(2026, 1, 2), base + timedelta(seconds=3), batch="e"),
        ]
        await _seed(factory, rows)

        n = await dw.drain_once(redis_url="redis://x", catalog_path="/cat")

        assert n == 4
        after = await _all_rows(factory)
        terminal_statuses = {r.status for r in after}
        assert terminal_statuses == {"completed"}, (
            f"all 4 rows must reach completed via the pipeline, got {terminal_statuses}"
        )

        # Pipeline actually ran 4 times — no silent skips.
        assert len(stub_pipeline) == 4
        # Round-robin order comes through at the pipeline layer, not just
        # at claim time — verifying that #165 fairness survives the real
        # execution path.
        symbols_in_order = [call["symbol"] for call in stub_pipeline]
        assert symbols_in_order == ["AAAUSDT", "BBBUSDT", "AAAUSDT", "BBBUSDT"], (
            f"expected round-robin execution across buckets, got {symbols_in_order}"
        )
