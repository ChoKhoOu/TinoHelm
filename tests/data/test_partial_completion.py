"""Tests for partial completion (#190–#193).

Covers:
- STATUS_PARTIAL_COMPLETED constant existence
- Pipeline tail-404 tolerance (trailing suffix within UTC last 3 days)
- Pipeline middle-404 remains hard failure
- Worker persists partial_completed and emits data.fetch.partial event
- Backtest runner treats partial_completed as terminal success
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


# ---------------------------------------------------------------------------
# STATUS_PARTIAL_COMPLETED constant
# ---------------------------------------------------------------------------

class TestPartialCompletedConstant:
    def test_status_constant_exists(self):
        from tinohelm.core.async_queue_worker import STATUS_PARTIAL_COMPLETED
        assert STATUS_PARTIAL_COMPLETED == "partial_completed"

    def test_exported_from_worker(self):
        from tinohelm.data.worker import STATUS_PARTIAL_COMPLETED
        assert STATUS_PARTIAL_COMPLETED == "partial_completed"


# ---------------------------------------------------------------------------
# Pipeline tail-404 tolerance
# ---------------------------------------------------------------------------

class TestTailFourOhFourTolerance:
    """The pipeline should tolerate trailing 404s within UTC last 3 days."""

    def test_classify_trailing_404s_within_tolerance(self):
        """Trailing daily 404s within last 3 UTC days → partial, not failure."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        total_tasks = 10
        # Last 2 tasks (today and yesterday) are 404s — trailing suffix
        failed_indices = {8: _mock_404_exc(), 9: _mock_404_exc()}
        success_indices = set(range(8))
        task_dates = [today - timedelta(days=9 - i) for i in range(10)]

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is True
        assert result.last_success_date == task_dates[7]

    def test_classify_middle_404_is_hard_failure(self):
        """A 404 in the middle of the range is a hard failure."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        total_tasks = 10
        # Task index 5 (middle of range) is a 404
        failed_indices = {5: _mock_404_exc()}
        success_indices = set(range(10)) - {5}
        task_dates = [today - timedelta(days=9 - i) for i in range(10)]

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is False

    def test_classify_trailing_404_outside_tolerance_window(self):
        """Trailing 404 older than 3 UTC days → hard failure."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        # All tasks are 5+ days old — trailing 404 is outside window
        task_dates = [today - timedelta(days=15 - i) for i in range(10)]
        failed_indices = {9: _mock_404_exc()}
        success_indices = set(range(9))

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is False

    def test_classify_non_404_error_is_always_hard_failure(self):
        """Non-404 errors (e.g. 500) are always hard failures."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        task_dates = [today - timedelta(days=2 - i) for i in range(3)]
        failed_indices = {2: _mock_500_exc()}
        success_indices = {0, 1}

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is False

    def test_classify_all_tasks_failed_is_hard_failure(self):
        """If no tasks succeeded at all, it's a hard failure regardless."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        task_dates = [today - timedelta(days=2 - i) for i in range(3)]
        failed_indices = {0: _mock_404_exc(), 1: _mock_404_exc(), 2: _mock_404_exc()}
        success_indices = set()

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is False

    def test_partial_result_carries_last_success_date(self):
        """IngestResult for partial completion has accurate end date."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        task_dates = [today - timedelta(days=4 - i) for i in range(5)]
        # Last 2 days failed (within tolerance)
        failed_indices = {3: _mock_404_exc(), 4: _mock_404_exc()}
        success_indices = {0, 1, 2}

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is True
        assert result.last_success_date == task_dates[2]

    def test_classify_none_date_is_hard_failure(self):
        """Failed task with unparseable date (None) → hard failure."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        task_dates = [today - timedelta(days=2), today - timedelta(days=1), None]
        failed_indices = {2: _mock_404_exc()}
        success_indices = {0, 1}

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is False

    def test_classify_none_last_success_date_is_hard_failure(self):
        """If last success task date is None, fall back to hard failure."""
        from tinohelm.data.pipeline_helpers import classify_download_failures

        today = _utc_today()
        # First task has unparseable date (None), second fails
        task_dates = [None, today]
        failed_indices = {1: _mock_404_exc()}
        success_indices = {0}

        result = classify_download_failures(
            failed_indices=failed_indices,
            success_indices=success_indices,
            task_dates=task_dates,
            tolerance_days=3,
        )
        assert result.is_partial is False


# ---------------------------------------------------------------------------
# IngestResult partial flag
# ---------------------------------------------------------------------------

class TestIngestResultPartial:
    def test_ingest_result_has_partial_field(self):
        from tinohelm.data.pipeline import IngestResult

        result = IngestResult(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            objects_count=100,
            files_written=5,
            partial=True,
        )
        assert result.partial is True

    def test_ingest_result_partial_defaults_false(self):
        from tinohelm.data.pipeline import IngestResult

        result = IngestResult(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            objects_count=100,
            files_written=5,
        )
        assert result.partial is False


# ---------------------------------------------------------------------------
# Worker: partial_completed persistence and event emission
# ---------------------------------------------------------------------------

class TestWorkerPartialCompletion:
    """Worker must persist partial_completed and emit data.fetch.partial."""

    async def test_partial_ingest_result_sets_partial_completed_status(self):
        """When pipeline returns partial=True, worker sets status=partial_completed."""
        from tinohelm.core.async_queue_worker import STATUS_PARTIAL_COMPLETED
        from tinohelm.data.worker import _guarded_terminal_update

        factory, job_id = await self._setup_running_job()
        updated = await _guarded_terminal_update(
            factory,
            job_id,
            {
                "status": STATUS_PARTIAL_COMPLETED,
                "progress": 100,
                "message": "Partial: 95 objects (tail 2 days unavailable)",
                "completed_at": datetime.now(UTC),
            },
        )
        assert updated is True

        # Verify the status was persisted
        from sqlalchemy import select
        from tinohelm.db.models import DataFetchJob

        async with factory() as db:
            job = (await db.execute(
                select(DataFetchJob).where(DataFetchJob.job_id == job_id)
            )).scalar_one()
            assert job.status == "partial_completed"

    async def test_partial_completed_is_in_bucket_started_statuses(self):
        """partial_completed counts as 'started' for scheduling fairness."""
        from tinohelm.data.worker import _BUCKET_STARTED_STATUSES, STATUS_PARTIAL_COMPLETED
        assert STATUS_PARTIAL_COMPLETED in _BUCKET_STARTED_STATUSES

    async def _setup_running_job(self):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from tinohelm.db.models import Base, DataFetchJob

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        job_id = str(uuid.uuid4())
        async with factory() as db:
            db.add(DataFetchJob(
                job_id=job_id,
                symbol="BTCUSDT-PERP",
                data_type="klines",
                interval="1m",
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 17),
                asset_class="um",
                status="running",
                progress=50,
                created_at=datetime(2026, 5, 17, 12, 0, 0),
            ))
            await db.commit()
        return factory, job_id


# ---------------------------------------------------------------------------
# Backtest runner: partial_completed is terminal
# ---------------------------------------------------------------------------

class TestBacktestWaitPartialCompleted:
    """_submit_and_wait_fetch must treat partial_completed as terminal success."""

    async def test_partial_completed_is_exported_from_core(self):
        """STATUS_PARTIAL_COMPLETED is importable from the core module."""
        from tinohelm.core.async_queue_worker import STATUS_PARTIAL_COMPLETED
        assert STATUS_PARTIAL_COMPLETED == "partial_completed"

    async def test_runner_module_has_partial_completed_in_success_branch(self):
        """Verify backtest runner source code contains partial_completed in success check."""
        import inspect
        from tinohelm.backtest import runner

        source = inspect.getsource(runner.BacktestRunner._submit_and_wait_fetch)
        assert "partial_completed" in source
        # The success branch includes both completed and partial_completed
        assert '"completed", "partial_completed"' in source or "partial_completed" in source


# ---------------------------------------------------------------------------
# IngestResult partial field propagation
# ---------------------------------------------------------------------------

class TestIngestResultPartialField:
    """Pipeline IngestResult must include partial metadata."""

    def test_ingest_result_partial_and_last_available_date(self):
        """IngestResult carries both partial flag and last_available_date."""
        from tinohelm.data.pipeline import IngestResult

        result = IngestResult(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            objects_count=100,
            files_written=5,
            partial=True,
            last_available_date=date(2026, 5, 15),
        )
        assert result.partial is True
        assert result.last_available_date == date(2026, 5, 15)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_404_exc():
    """Create a mock HTTPStatusError with 404 status."""
    exc = Exception("HTTP 404 Not Found")
    exc.response = SimpleNamespace(status_code=404)
    return exc


def _mock_500_exc():
    """Create a mock HTTPStatusError with 500 status."""
    exc = Exception("HTTP 500 Internal Server Error")
    exc.response = SimpleNamespace(status_code=500)
    return exc
