"""Issue #163: backtest-triggered single-job fetch forms a single-job FetchBatch.

Every ``BacktestRunner._submit_and_wait_fetch`` call must:
  1. Tag the created ``DataFetchJob`` with a non-empty ``batch_id``.
  2. Produce a fresh ``batch_id`` per call (two separate submissions ⇒ two
     distinct FetchBatch identities).

This pins down the "backtest standalone fetch = single-job FetchBatch"
contract from PRD #162 implementation decision: *Treat backtest-triggered
standalone fetches as single-job FetchBatch instances rather than giving
them a special scheduling path.*
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from tinohelm.backtest.runner import BacktestRunner


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def _mk_runner() -> BacktestRunner:
    runner = BacktestRunner(
        strategy_path="fake/strat.py:FakeStrategy",
        config_path="fake/strat.py:FakeStrategyConfig",
        symbols=["BTCUSDT-PERP"],
        intervals=["1m"],
        start=_utc(2024, 1, 1),
        end=_utc(2024, 1, 2),
    )
    runner._redis_client = MagicMock()
    return runner


class _CapturingSession:
    """Stand-in for an AsyncSession that records added DataFetchJob rows.

    First ``execute`` returns no job (job not yet persisted by a worker);
    subsequent executes return a completed row so the poll loop exits.
    """

    def __init__(self, added: list, state: dict) -> None:
        self._added = added
        self._state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def add(self, obj) -> None:
        self._added.append(obj)
        self._state["last_added"] = obj

    async def commit(self) -> None:
        pass

    async def execute(self, stmt):
        # Return the most-recently-added job or batch rows with status=completed
        # so the poll loop exits on the first iteration.
        job = self._state.get("last_added")
        if job is not None:
            job.status = "completed"
            job.message = "ok"
        rows = list(self._added)
        for row in rows:
            row.status = "completed"
            row.message = "ok"

        class _Result:
            def __init__(self, row, rows):
                self._row = row
                self._rows = rows

            def scalar_one_or_none(self):
                return self._row

            def scalars(self):
                class _Scalars:
                    def __init__(self, rows):
                        self._rows = rows

                    def all(self_inner):
                        return list(self_inner._rows)

                return _Scalars(self._rows)

        return _Result(job, rows)


def _install_capturing_db(monkeypatch, added: list) -> None:
    """Patch the async engine + sessionmaker used inside ``_submit_and_wait_fetch``.

    We monkeypatch at the sqlalchemy.ext.asyncio module boundary because the
    method imports those names locally inside the function body.
    """
    state: dict = {}

    class _FakeEngine:
        async def dispose(self):
            pass

    def _fake_create_async_engine(*args, **kwargs):
        return _FakeEngine()

    def _fake_sessionmaker(*args, **kwargs):
        def _factory():
            return _CapturingSession(added, state)

        return _factory

    import sqlalchemy.ext.asyncio as _ext

    monkeypatch.setattr(_ext, "create_async_engine", _fake_create_async_engine)
    monkeypatch.setattr(_ext, "async_sessionmaker", _fake_sessionmaker)


class TestBacktestSubmitAndWaitFetchBatchId:
    def test_submit_skips_job_creation_when_no_missing_slices(self, monkeypatch):
        runner = _mk_runner()
        added: list = []
        _install_capturing_db(monkeypatch, added)
        async def _no_missing(**_kwargs):
            return []

        monkeypatch.setattr(
            "tinohelm.data.coverage.plan_submission_slices",
            _no_missing,
        )

        ok = asyncio.run(runner._submit_and_wait_fetch("BTCUSDT-PERP", "1m"))

        assert ok is True
        assert added == []
        assert not runner._redis_client.lpush.called

    def test_submit_creates_one_job_per_missing_slice(self, monkeypatch):
        runner = _mk_runner()
        added: list = []
        _install_capturing_db(monkeypatch, added)

        async def _two_slices(**_kwargs):
            return [
                (datetime(2024, 1, 1, tzinfo=timezone.utc).date(), datetime(2024, 1, 1, tzinfo=timezone.utc).date()),
                (datetime(2024, 1, 2, tzinfo=timezone.utc).date(), datetime(2024, 1, 2, tzinfo=timezone.utc).date()),
            ]

        monkeypatch.setattr("tinohelm.data.coverage.plan_submission_slices", _two_slices)

        ok = asyncio.run(runner._submit_and_wait_fetch("BTCUSDT-PERP", "1m"))

        assert ok is True
        assert len(added) == 2
        assert [(job.start_date, job.end_date) for job in added] == [
            (datetime(2024, 1, 1, tzinfo=timezone.utc).date(), datetime(2024, 1, 1, tzinfo=timezone.utc).date()),
            (datetime(2024, 1, 2, tzinfo=timezone.utc).date(), datetime(2024, 1, 2, tzinfo=timezone.utc).date()),
        ]

    def test_single_submit_assigns_non_empty_batch_id(self, monkeypatch):
        runner = _mk_runner()
        added: list = []
        _install_capturing_db(monkeypatch, added)

        ok = asyncio.run(runner._submit_and_wait_fetch("BTCUSDT-PERP", "1m"))

        assert ok is True
        assert len(added) == 1
        job = added[0]
        assert job.batch_id, "backtest-created DataFetchJob must carry a non-empty batch_id"
        assert isinstance(job.batch_id, str)
        assert len(job.batch_id) == 36  # UUID shape

    def test_two_submissions_get_distinct_batch_ids(self, monkeypatch):
        runner = _mk_runner()
        added: list = []
        _install_capturing_db(monkeypatch, added)

        async def _one_slice(**_kwargs):
            return [(datetime(2024, 1, 1, tzinfo=timezone.utc).date(), datetime(2024, 1, 1, tzinfo=timezone.utc).date())]

        monkeypatch.setattr("tinohelm.data.coverage.plan_submission_slices", _one_slice)

        assert asyncio.run(runner._submit_and_wait_fetch("BTCUSDT-PERP", "1m"))
        assert asyncio.run(runner._submit_and_wait_fetch("BTCUSDT-PERP", "5m"))

        assert len(added) >= 2
        batch_ids = {job.batch_id for job in added}
        assert len(batch_ids) == 2, (
            "each backtest-triggered fetch is its own FetchBatch → "
            f"batch_ids must differ, got {batch_ids}"
        )

    def test_batch_id_differs_from_job_id(self, monkeypatch):
        """``batch_id`` and ``job_id`` are independent identifiers."""
        runner = _mk_runner()
        added: list = []
        _install_capturing_db(monkeypatch, added)

        asyncio.run(runner._submit_and_wait_fetch("BTCUSDT-PERP", "1m"))

        job = added[0]
        assert job.batch_id != job.job_id

    def test_submit_pushes_wake_token_not_job_id(self, monkeypatch):
        """Issue #164: backtest also speaks the wake-signal dialect.

        Before #164 the backtest runner LPUSH'd the job_id directly onto
        ``tino:data:queue`` so the worker could BRPOP it off. After the
        DB-driven scheduler lands, Redis carries only wake sentinels and
        the DB owns ordering — so the backtest runner must push the same
        token every other caller emits, not the job_id.
        """
        from tinohelm.data import worker as dw

        runner = _mk_runner()
        added: list = []
        _install_capturing_db(monkeypatch, added)

        asyncio.run(runner._submit_and_wait_fetch("BTCUSDT-PERP", "1m"))

        fake_redis = runner._redis_client
        assert fake_redis.lpush.called, "backtest must wake the worker exactly once"
        assert fake_redis.lpush.call_count == 1
        call = fake_redis.lpush.call_args
        assert call.args[0] == "tino:data:queue"
        pushed_value = call.args[1]
        job = added[0]
        assert pushed_value != job.job_id, (
            "backtest runner must not push job_id under the new scheduler"
        )
        assert pushed_value == dw.WAKE_TOKEN
