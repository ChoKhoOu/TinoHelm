"""Tests for tinohelm.data.pipeline.BinanceVisionPipeline.

All network, DB, and filesystem side-effects are mocked.
"""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinohelm.data.pipeline import BinanceVisionPipeline, IngestResult


# ---------------------------------------------------------------------------
# 1. BinanceVisionPipeline init
# ---------------------------------------------------------------------------

class TestPipelineInit:
    def test_catalog_path_stored_as_str(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        assert p.catalog_path == "/tmp/test_catalog"

    def test_catalog_path_from_pathlib(self, tmp_path):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        assert p.catalog_path == str(tmp_path)

    def test_downloader_created(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        assert p.downloader is not None

    def test_custom_raw_dir(self, tmp_path):
        raw = tmp_path / "raw"
        p = BinanceVisionPipeline(catalog_path="/tmp/x", raw_dir=str(raw))
        assert p.downloader.raw_dir == raw


# ---------------------------------------------------------------------------
# 2. IngestResult dataclass
# ---------------------------------------------------------------------------

class TestIngestResult:
    def test_defaults(self):
        r = IngestResult(symbol="X", data_type="klines", objects_count=0, files_written=0)
        assert r.skipped is False
        assert r.rest_fallback_used is False
        assert r.rest_fallback_range is None
        assert r.file_paths == []
        assert r.start is None
        assert r.end is None

    def test_explicit_fields(self):
        r = IngestResult(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            objects_count=1234,
            files_written=3,
            file_paths=["/a", "/b", "/c"],
            start=date(2025, 1, 1),
            end=date(2025, 1, 31),
            skipped=True,
            rest_fallback_used=True,
            rest_fallback_range=(date(2025, 1, 28), date(2025, 1, 31)),
        )
        assert r.objects_count == 1234
        assert r.files_written == 3
        assert r.skipped is True
        assert r.rest_fallback_used is True
        assert r.rest_fallback_range == (date(2025, 1, 28), date(2025, 1, 31))


# ---------------------------------------------------------------------------
# 3. ingest() — klines requires interval
# ---------------------------------------------------------------------------

class TestIngestValidation:
    def test_ingest_requires_interval_for_klines(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="klines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

    def test_ingest_requires_interval_for_mark_price_klines(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="markPriceKlines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

    def test_ingest_requires_interval_for_index_price_klines(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="indexPriceKlines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

    def test_ingest_no_interval_required_for_agg_trades(self):
        """aggTrades does not need interval — should not raise ValueError on that check."""
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        # We patch everything deep so we just test the validation path exits cleanly
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []  # No tasks → returns early
        p.downloader = mock_dl

        # Patch _update_db_catalog to avoid DB access
        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="aggTrades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 5),
            ))

        assert result.skipped is True


# ---------------------------------------------------------------------------
# 4. ingest() — early return when no tasks
# ---------------------------------------------------------------------------

class TestIngestNoTasks:
    def test_returns_skipped_result_when_no_download_tasks(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            ))

        assert result.skipped is True
        assert result.objects_count == 0
        assert result.files_written == 0
        assert result.symbol == "BTCUSDT-PERP"
        assert result.data_type == "fundingRate"


# ---------------------------------------------------------------------------
# 5. ingest_sync — sync wrapper
# ---------------------------------------------------------------------------

class TestIngestSync:
    def test_sync_wrapper_exists(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        assert hasattr(p, "ingest_sync")
        assert callable(p.ingest_sync)

    def test_sync_wrapper_calls_ingest(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        expected = IngestResult(
            symbol="BTCUSDT-PERP", data_type="fundingRate",
            objects_count=0, files_written=0, skipped=True,
        )

        async def _fake_ingest(**kwargs):
            return expected

        with patch.object(p, "ingest", side_effect=_fake_ingest):
            result = p.ingest_sync(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            )

        assert result is expected

    def test_sync_wrapper_propagates_value_error(self):
        """interval validation raises before any async work — sync wrapper propagates it."""
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        with pytest.raises(ValueError, match="interval is required"):
            p.ingest_sync(
                symbol="BTCUSDT-PERP",
                data_type="klines",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
            )


# ---------------------------------------------------------------------------
# 6. progress_cb — callback is invoked
# ---------------------------------------------------------------------------

class TestProgressCallback:
    def test_progress_cb_called(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        calls: list[tuple[int, str]] = []

        def _cb(pct: int, msg: str):
            calls.append((pct, msg))

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
                progress_cb=_cb,
            ))

        # At minimum the 0% and 100% progress calls must have been made
        pcts = [c[0] for c in calls]
        assert 0 in pcts
        assert 100 in pcts

    def test_async_progress_cb_accepted(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        calls: list[tuple[int, str]] = []

        async def _async_cb(pct: int, msg: str):
            calls.append((pct, msg))

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2025, 1, 1),
                end=date(2025, 1, 31),
                progress_cb=_async_cb,
            ))

        # Async callback must also be awaited properly
        assert len(calls) > 0


# ---------------------------------------------------------------------------
# 7. _detect_vision_coverage_end
# ---------------------------------------------------------------------------

class TestDetectVisionCoverageEnd:
    def _make_task(self, granularity: str, stem: str) -> MagicMock:
        task = MagicMock()
        task.granularity = granularity
        task.dest_path = MagicMock()
        task.dest_path.stem = stem
        return task

    def test_daily_task_parses_date(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        task = self._make_task("daily", "BTCUSDT-aggTrades-2025-03-15")
        result = p._detect_vision_coverage_end(tasks=[task])
        assert result == date(2025, 3, 15)

    def test_monthly_task_returns_last_day_of_month(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        task = self._make_task("monthly", "BTCUSDT-klines-1m-2025-03")
        result = p._detect_vision_coverage_end(tasks=[task])
        assert result == date(2025, 3, 31)

    def test_monthly_december_returns_dec_31(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        task = self._make_task("monthly", "BTCUSDT-klines-1m-2024-12")
        result = p._detect_vision_coverage_end(tasks=[task])
        assert result == date(2024, 12, 31)

    def test_empty_tasks_returns_none(self):
        p = BinanceVisionPipeline(catalog_path="/tmp/x")
        result = p._detect_vision_coverage_end(tasks=[])
        assert result is None
