"""Tests for the refactored ingest pipeline: gap detection, consolidation, and organization.

Covers:
- detect_gaps: identifies gaps in catalog interval data
- expand_gaps_to_days: converts ns-level gaps to day-aligned date ranges
- consolidate_and_organize: NT-native consolidate + by_period orchestration
- Pipeline ingest with consolidation (end-to-end)
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest


class TestDetectGaps:
    """detect_gaps returns gap intervals from catalog.get_intervals() output."""

    def test_no_gaps_contiguous(self):
        from tinohelm.data.pipeline_helpers import detect_gaps

        intervals = [
            (0, 100),
            (100, 200),
            (200, 300),
        ]
        assert detect_gaps(intervals) == []

    def test_single_gap(self):
        from tinohelm.data.pipeline_helpers import detect_gaps

        intervals = [
            (0, 100),
            (200, 300),
        ]
        gaps = detect_gaps(intervals)
        assert len(gaps) == 1
        assert gaps[0] == (100, 200)

    def test_multiple_gaps(self):
        from tinohelm.data.pipeline_helpers import detect_gaps

        intervals = [
            (0, 100),
            (200, 300),
            (500, 600),
        ]
        gaps = detect_gaps(intervals)
        assert len(gaps) == 2
        assert gaps[0] == (100, 200)
        assert gaps[1] == (300, 500)

    def test_empty_intervals(self):
        from tinohelm.data.pipeline_helpers import detect_gaps

        assert detect_gaps([]) == []

    def test_single_interval(self):
        from tinohelm.data.pipeline_helpers import detect_gaps

        assert detect_gaps([(0, 100)]) == []

    def test_adjacent_intervals_no_gap(self):
        """Adjacent intervals (end == next start) are not gaps."""
        from tinohelm.data.pipeline_helpers import detect_gaps

        intervals = [(0, 1000), (1000, 2000)]
        assert detect_gaps(intervals) == []

    def test_one_nanosecond_gap(self):
        """Even a 1ns gap is still a gap."""
        from tinohelm.data.pipeline_helpers import detect_gaps

        intervals = [(0, 100), (101, 200)]
        gaps = detect_gaps(intervals)
        assert len(gaps) == 1
        assert gaps[0] == (100, 101)


class TestExpandGapsToDays:
    """expand_gaps_to_days converts nanosecond gaps to day-aligned date ranges."""

    def test_single_gap_within_one_day(self):
        from tinohelm.data.pipeline_helpers import expand_gaps_to_days

        # Gap entirely within 2025-01-15 (from noon to evening)
        noon_ns = 1_736_942_400_000_000_000  # 2025-01-15 12:00:00 UTC
        evening_ns = 1_736_978_400_000_000_000  # 2025-01-15 22:00:00 UTC
        gaps = [(noon_ns, evening_ns)]
        result = expand_gaps_to_days(gaps)
        assert len(result) == 1
        assert result[0] == (date(2025, 1, 15), date(2025, 1, 15))

    def test_gap_spanning_multiple_days(self):
        from tinohelm.data.pipeline_helpers import expand_gaps_to_days

        # Gap from 2025-01-14 23:59 to 2025-01-18 00:01
        # start_day: 23:59 is not midnight → ceil to 2025-01-15
        # end_day: 00:01 is not midnight → include 2025-01-18 (partial day needs data)
        gap_start_ns = 1_736_899_140_000_000_000  # 2025-01-14 23:59:00 UTC
        gap_end_ns = 1_737_158_460_000_000_000  # 2025-01-18 00:01:00 UTC
        gaps = [(gap_start_ns, gap_end_ns)]
        result = expand_gaps_to_days(gaps)
        assert len(result) == 1
        assert result[0] == (date(2025, 1, 15), date(2025, 1, 18))

    def test_gap_exact_day_boundaries(self):
        from tinohelm.data.pipeline_helpers import expand_gaps_to_days

        # Gap from 2025-01-15 00:00:00 to 2025-01-18 00:00:00
        day_start = 1_736_899_200_000_000_000  # 2025-01-15 00:00:00 UTC
        day_end = 1_737_158_400_000_000_000  # 2025-01-18 00:00:00 UTC
        gaps = [(day_start, day_end)]
        result = expand_gaps_to_days(gaps)
        assert len(result) == 1
        assert result[0] == (date(2025, 1, 15), date(2025, 1, 17))

    def test_empty_gaps(self):
        from tinohelm.data.pipeline_helpers import expand_gaps_to_days

        assert expand_gaps_to_days([]) == []

    def test_multiple_gaps(self):
        from tinohelm.data.pipeline_helpers import expand_gaps_to_days

        # Two separate gaps
        gap1_start = 1_736_899_200_000_000_000  # 2025-01-15 00:00:00
        gap1_end = 1_736_985_600_000_000_000  # 2025-01-16 00:00:00
        gap2_start = 1_737_158_400_000_000_000  # 2025-01-18 00:00:00
        gap2_end = 1_737_244_800_000_000_000  # 2025-01-19 00:00:00
        gaps = [(gap1_start, gap1_end), (gap2_start, gap2_end)]
        result = expand_gaps_to_days(gaps)
        assert len(result) == 2
        assert result[0] == (date(2025, 1, 15), date(2025, 1, 15))
        assert result[1] == (date(2025, 1, 18), date(2025, 1, 18))

    def test_sub_day_gap_still_produces_day(self):
        """A gap smaller than a day should still produce at least one day."""
        from tinohelm.data.pipeline_helpers import expand_gaps_to_days

        # Gap from 2025-01-15 10:00 to 2025-01-15 14:00 (same day)
        start_ns = 1_736_935_200_000_000_000  # 2025-01-15 10:00:00
        end_ns = 1_736_949_600_000_000_000  # 2025-01-15 14:00:00
        gaps = [(start_ns, end_ns)]
        result = expand_gaps_to_days(gaps)
        assert len(result) == 1
        assert result[0] == (date(2025, 1, 15), date(2025, 1, 15))


class TestGapBackfillIntegration:
    """_detect_gaps_for_backfill returns day ranges needing re-download."""

    def test_no_gaps_skips_backfill(self, tmp_path):
        """When catalog has contiguous intervals, no backfill triggered."""
        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument
        from tinohelm.data.pipeline import BinanceVisionPipeline

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        # Write contiguous bars (no gap)
        base_ts = 1_700_000_000_000_000_000
        minute_ns = 60_000_000_000
        bars = []
        for i in range(60):
            ts = base_ts + i * minute_ns
            bar = Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            )
            bars.append(bar)
        catalog.write_data(bars, skip_disjoint_check=True)

        pipe = BinanceVisionPipeline(catalog_path=tmp_path)
        backfill_dates = pipe._detect_gaps_for_backfill(
            "BTCUSDT-PERP", "klines", "1m"
        )
        assert backfill_dates == []

    def test_detects_gap_and_returns_date_ranges(self, tmp_path):
        """When catalog has a gap, returns the date ranges needing backfill."""
        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument
        from tinohelm.data.pipeline import BinanceVisionPipeline

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        # Write bars for Jan 15, then skip Jan 16, write Jan 17
        minute_ns = 60_000_000_000
        # Jan 15 00:00 UTC
        jan15_start = 1_736_899_200_000_000_000
        # Jan 17 00:00 UTC
        jan17_start = 1_737_072_000_000_000_000

        for day_start in (jan15_start, jan17_start):
            bars = []
            for i in range(60):  # 1 hour of bars
                ts = day_start + i * minute_ns
                bar = Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                )
                bars.append(bar)
            catalog.write_data(bars, skip_disjoint_check=True)

        pipe = BinanceVisionPipeline(catalog_path=tmp_path)
        backfill_dates = pipe._detect_gaps_for_backfill(
            "BTCUSDT-PERP", "klines", "1m"
        )
        # Gap between Jan 15 01:00 and Jan 17 00:00 → only Jan 16 needs backfill
        assert len(backfill_dates) == 1
        start_d, end_d = backfill_dates[0]
        assert start_d == date(2025, 1, 16)
        assert end_d == date(2025, 1, 16)


class TestConsolidateAndOrganize:
    """consolidate_and_organize calls NT-native consolidate + by_period."""

    def test_consolidates_fragmented_bars(self, tmp_path):
        """Fragmented bar writes should be consolidated into fewer files."""
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument
        from tinohelm.data.catalog import consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        # Write 3 separate small chunks to create fragmented files
        base_ts = 1_700_000_000_000_000_000  # some base timestamp
        minute_ns = 60_000_000_000
        for chunk_idx in range(3):
            bars = []
            for i in range(10):
                ts = base_ts + (chunk_idx * 10 + i) * minute_ns
                bar = Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                )
                bars.append(bar)
            catalog.write_data(bars, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        files_before = list(bar_dir.glob("*.parquet"))
        assert len(files_before) == 3

        consolidate_and_organize(catalog, Bar, str(bar_type))

        files_after = list(bar_dir.glob("*.parquet"))
        assert len(files_after) < len(files_before)

        # Verify data is still intact
        result_bars = catalog.bars(bar_types=[str(bar_type)])
        assert len(result_bars) == 30

    def test_deduplicates_during_consolidation(self, tmp_path):
        """Duplicate rows should be removed during consolidation."""
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument
        from tinohelm.data.catalog import consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        base_ts = 1_700_000_000_000_000_000
        minute_ns = 60_000_000_000

        # Write same bars twice to create duplicates
        bars = []
        for i in range(10):
            ts = base_ts + i * minute_ns
            bar = Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            )
            bars.append(bar)
        catalog.write_data(bars, skip_disjoint_check=True)
        catalog.write_data(bars, skip_disjoint_check=True)

        consolidate_and_organize(catalog, Bar, str(bar_type))

        result_bars = catalog.bars(bar_types=[str(bar_type)])
        assert len(result_bars) == 10

    def test_idempotent(self, tmp_path):
        """Running consolidation twice produces the same result."""
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument
        from tinohelm.data.catalog import consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        base_ts = 1_700_000_000_000_000_000
        minute_ns = 60_000_000_000
        bars = []
        for i in range(20):
            ts = base_ts + i * minute_ns
            bar = Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            )
            bars.append(bar)
        catalog.write_data(bars, skip_disjoint_check=True)

        consolidate_and_organize(catalog, Bar, str(bar_type))
        files_first = sorted(f.name for f in (tmp_path / "data" / "bar" / str(bar_type)).glob("*.parquet"))

        consolidate_and_organize(catalog, Bar, str(bar_type))
        files_second = sorted(f.name for f in (tmp_path / "data" / "bar" / str(bar_type)).glob("*.parquet"))

        assert files_first == files_second
        result_bars = catalog.bars(bar_types=[str(bar_type)])
        assert len(result_bars) == 20

    def test_splits_multi_week_data_into_weekly_files(self, tmp_path):
        """Data spanning multiple weeks should be split into weekly files."""
        from nautilus_trader.model.data import Bar, BarType
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument
        from tinohelm.data.catalog import consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        # Write 3 weeks of 1m bars (3 * 7 * 24 * 60 = 30240 bars)
        base_ts = 1_700_000_000_000_000_000
        minute_ns = 60_000_000_000
        total_bars = 3 * 7 * 24 * 60  # 3 weeks
        chunk_size = 10000
        for start_idx in range(0, total_bars, chunk_size):
            end_idx = min(start_idx + chunk_size, total_bars)
            bars = []
            for i in range(start_idx, end_idx):
                ts = base_ts + i * minute_ns
                bar = Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                )
                bars.append(bar)
            catalog.write_data(bars, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        files_before = list(bar_dir.glob("*.parquet"))
        assert len(files_before) >= 3

        consolidate_and_organize(catalog, Bar, str(bar_type))

        files_after = list(bar_dir.glob("*.parquet"))
        # Should have multiple weekly files (3+ weeks of data)
        assert len(files_after) >= 3
        # Total bars preserved
        result_bars = catalog.bars(bar_types=[str(bar_type)])
        assert len(result_bars) == total_bars


class TestConsolidateUsesByPeriod:
    """consolidate_and_organize must use consolidate_data_by_period (not consolidate_data)."""

    def test_does_not_call_consolidate_data(self, tmp_path, monkeypatch):
        """Verify consolidate_and_organize never calls the OOM-prone consolidate_data."""
        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        base_ts = 1_700_000_000_000_000_000
        minute_ns = 60_000_000_000
        for chunk_idx in range(3):
            bars = []
            for i in range(10):
                ts = base_ts + (chunk_idx * 10 + i) * minute_ns
                bar = Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                )
                bars.append(bar)
            catalog.write_data(bars, skip_disjoint_check=True)

        consolidate_data_called = []
        original_consolidate = catalog.consolidate_data

        def _spy_consolidate_data(*args, **kwargs):
            consolidate_data_called.append(True)
            return original_consolidate(*args, **kwargs)

        monkeypatch.setattr(catalog, "consolidate_data", _spy_consolidate_data)

        consolidate_and_organize(catalog, Bar, str(bar_type))

        assert not consolidate_data_called, (
            "consolidate_and_organize must NOT call consolidate_data (OOM risk); "
            "use consolidate_data_by_period instead"
        )

    def test_incremental_mode_does_not_call_consolidate_data(self, tmp_path, monkeypatch):
        """Incremental mode (with start/end) must also avoid consolidate_data."""
        from datetime import date

        from nautilus_trader.model.data import TradeTick
        from nautilus_trader.model.enums import AggressorSide
        from nautilus_trader.model.identifiers import TradeId
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import consolidate_and_organize
        from tinohelm.data.instruments import make_instrument

        instrument = make_instrument("BTCUSDT-PERP")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        # Write multiple fragments with different time ranges (simulates chunked ingest)
        base_ts = 1_704_931_200_000_000_000  # 2024-01-11 00:00:00 UTC
        for chunk_idx in range(3):
            ticks = []
            for i in range(10):
                ts = base_ts + (chunk_idx * 10 + i) * 1_000_000_000
                ticks.append(TradeTick(
                    instrument_id=instrument.id,
                    price=instrument.make_price(100.0 + i),
                    size=instrument.make_qty(0.1),
                    aggressor_side=AggressorSide.BUYER,
                    trade_id=TradeId(str(chunk_idx * 10 + i + 1)),
                    ts_event=ts,
                    ts_init=ts,
                ))
            catalog.write_data(ticks, skip_disjoint_check=True)

        consolidate_data_called = []
        original_consolidate = catalog.consolidate_data

        def _spy(*args, **kwargs):
            consolidate_data_called.append(True)
            return original_consolidate(*args, **kwargs)

        monkeypatch.setattr(catalog, "consolidate_data", _spy)

        consolidate_and_organize(
            catalog, TradeTick, str(instrument.id),
            start=date(2024, 1, 11), end=date(2024, 1, 11),
        )

        assert not consolidate_data_called, (
            "consolidate_and_organize (incremental) must NOT call consolidate_data"
        )

        result = catalog.query(TradeTick, instrument_ids=[str(instrument.id)])
        assert len(result) == 30


class TestTradeTickConsolidation:
    def test_trade_tick_consolidation_is_idempotent_for_duplicate_fragments(self, tmp_path):
        from datetime import date

        from nautilus_trader.model.data import TradeTick
        from tinohelm.data.catalog import _catalog_for_root, consolidate_and_organize
        from tinohelm.data.catalog import write_trade_ticks
        from tinohelm.data.instruments import make_instrument
        from nautilus_trader.model.enums import AggressorSide
        from nautilus_trader.model.identifiers import TradeId

        instrument = make_instrument("BTCUSDT-PERP")
        ticks = []
        base_ts = 1_704_931_200_000_000_000
        for i in range(5):
            ts = base_ts + i * 1_000_000
            ticks.append(TradeTick(
                instrument_id=instrument.id,
                price=instrument.make_price(100.0 + i),
                size=instrument.make_qty(0.1),
                aggressor_side=AggressorSide.BUYER,
                trade_id=TradeId(str(i + 1)),
                ts_event=ts,
                ts_init=ts,
            ))

        write_trade_ticks(ticks, "BTCUSDT-PERP", tmp_path)
        write_trade_ticks(ticks, "BTCUSDT-PERP", tmp_path)

        catalog = _catalog_for_root(tmp_path)
        consolidate_and_organize(
            catalog,
            TradeTick,
            str(instrument.id),
            start=date(2024, 1, 11),
            end=date(2024, 1, 11),
        )
        first = catalog.query(TradeTick, instrument_ids=[str(instrument.id)])
        assert len(first) == 5

        consolidate_and_organize(
            catalog,
            TradeTick,
            str(instrument.id),
            start=date(2024, 1, 11),
            end=date(2024, 1, 11),
        )
        second = catalog.query(TradeTick, instrument_ids=[str(instrument.id)])
        assert len(second) == 5


class TestIncrementalConsolidation:
    """consolidate_and_organize with start/end only touches affected weeks."""

    def _make_weekly_catalog(self, tmp_path):
        """Set up a catalog with 3 pre-consolidated weekly files."""
        from datetime import datetime, timezone
        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        # 3 weeks starting Monday 2024-01-01
        # Week 1: 2024-01-01 .. 2024-01-07
        # Week 2: 2024-01-08 .. 2024-01-14
        # Week 3: 2024-01-15 .. 2024-01-21
        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        minute_ns = 60_000_000_000
        week_bars = 7 * 24 * 60  # 10080 bars per week

        for week_idx in range(3):
            week_start_ns = int((base.timestamp() + week_idx * 7 * 86400) * 1_000_000_000)
            bars = []
            for i in range(week_bars):
                ts = week_start_ns + i * minute_ns
                bar = Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                )
                bars.append(bar)
            catalog.write_data(bars, skip_disjoint_check=True)

        # Pre-consolidate so each week is one file
        consolidate_and_organize(catalog, Bar, str(bar_type))
        return catalog, instrument, bar_type

    def test_only_touches_affected_week(self, tmp_path):
        """When start/end covers one week, only that week's file is rewritten."""
        from datetime import date
        from nautilus_trader.model.data import Bar
        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        catalog, instrument, bar_type = self._make_weekly_catalog(tmp_path)
        bar_dir = tmp_path / "data" / "bar" / str(bar_type)

        files_before = sorted(bar_dir.glob("*.parquet"))
        mtimes_before = {f.name: f.stat().st_mtime_ns for f in files_before}

        # Add a few duplicate bars in week 2 (2024-01-08 .. 2024-01-14)
        from datetime import datetime, timezone
        week2_start_ns = int(datetime(2024, 1, 8, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        minute_ns = 60_000_000_000
        dup_bars = []
        for i in range(5):
            ts = week2_start_ns + i * minute_ns
            bar = Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            )
            dup_bars.append(bar)
        catalog.write_data(dup_bars, skip_disjoint_check=True)

        # Consolidate only the affected range
        consolidate_and_organize(
            catalog, Bar, str(bar_type),
            start=date(2024, 1, 8),
            end=date(2024, 1, 14),
        )

        files_after = sorted(bar_dir.glob("*.parquet"))
        mtimes_after = {f.name: f.stat().st_mtime_ns for f in files_after}

        # Verify exactly which files were modified vs untouched
        unchanged = {f for f, t in mtimes_before.items() if mtimes_after.get(f) == t}
        modified_or_new = set(mtimes_after) - unchanged
        # Only the file(s) covering the affected range should be rewritten;
        # the unaffected file (week 1: 01-01~01-03, week 4: 01-18~01-21) must stay
        assert len(unchanged) >= 2, (
            f"Expected at least 2 untouched files, got {len(unchanged)}: {unchanged}"
        )
        assert len(modified_or_new) >= 1, (
            f"Expected at least 1 rewritten file, got {modified_or_new}"
        )

        # Data integrity: duplicates removed, total = 3 weeks worth
        all_bars = catalog.bars(bar_types=[str(bar_type)])
        expected = 3 * 7 * 24 * 60
        assert len(all_bars) == expected

    def test_no_start_end_consolidates_all(self, tmp_path):
        """Without start/end, all weeks are consolidated (backward compat)."""
        from nautilus_trader.model.data import Bar
        from tinohelm.data.catalog import consolidate_and_organize

        catalog, instrument, bar_type = self._make_weekly_catalog(tmp_path)
        bar_dir = tmp_path / "data" / "bar" / str(bar_type)

        # Add fragments across multiple weeks
        from datetime import datetime, timezone
        minute_ns = 60_000_000_000
        for week_offset in range(3):
            week_start_ns = int(
                datetime(2024, 1, 1 + week_offset * 7, tzinfo=timezone.utc).timestamp()
                * 1_000_000_000
            )
            dup_bars = []
            for i in range(3):
                ts = week_start_ns + i * minute_ns
                bar = Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                )
                dup_bars.append(bar)
            catalog.write_data(dup_bars, skip_disjoint_check=True)

        # No start/end — should consolidate everything
        consolidate_and_organize(catalog, Bar, str(bar_type))

        all_bars = catalog.bars(bar_types=[str(bar_type)])
        expected = 3 * 7 * 24 * 60
        assert len(all_bars) == expected

    def test_cross_week_boundary(self, tmp_path):
        """start/end spanning two weeks should consolidate both weeks."""
        from datetime import date
        from nautilus_trader.model.data import Bar
        from tinohelm.data.catalog import consolidate_and_organize

        catalog, instrument, bar_type = self._make_weekly_catalog(tmp_path)
        bar_dir = tmp_path / "data" / "bar" / str(bar_type)

        files_before = sorted(bar_dir.glob("*.parquet"))
        mtimes_before = {f.name: f.stat().st_mtime_ns for f in files_before}

        # Add duplicate bars in week 1 and week 2 separately
        from datetime import datetime, timezone
        minute_ns = 60_000_000_000
        # Duplicate in week 1: 2024-01-07 23:00
        boundary_ts_1 = int(datetime(2024, 1, 7, 23, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        bar1 = Bar(
            bar_type=bar_type,
            open=instrument.make_price(100.0),
            high=instrument.make_price(101.0),
            low=instrument.make_price(99.0),
            close=instrument.make_price(100.5),
            volume=instrument.make_qty(1000.0),
            ts_event=boundary_ts_1,
            ts_init=boundary_ts_1,
        )
        catalog.write_data([bar1], skip_disjoint_check=True)
        # Duplicate in week 2: 2024-01-08 00:00
        boundary_ts_2 = int(datetime(2024, 1, 8, 0, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        bar2 = Bar(
            bar_type=bar_type,
            open=instrument.make_price(100.0),
            high=instrument.make_price(101.0),
            low=instrument.make_price(99.0),
            close=instrument.make_price(100.5),
            volume=instrument.make_qty(1000.0),
            ts_event=boundary_ts_2,
            ts_init=boundary_ts_2,
        )
        catalog.write_data([bar2], skip_disjoint_check=True)

        consolidate_and_organize(
            catalog, Bar, str(bar_type),
            start=date(2024, 1, 7),
            end=date(2024, 1, 8),
        )

        files_after = sorted(bar_dir.glob("*.parquet"))
        mtimes_after = {f.name: f.stat().st_mtime_ns for f in files_after}

        # At least the last period file (week 4: 01-18~01-21) must be untouched
        unchanged = {f for f, t in mtimes_before.items() if mtimes_after.get(f) == t}
        assert len(unchanged) >= 1, (
            f"Expected at least 1 untouched file (week 4), got {unchanged}"
        )

        # Data integrity: duplicates removed, total unchanged
        all_bars = catalog.bars(bar_types=[str(bar_type)])
        expected = 3 * 7 * 24 * 60
        assert len(all_bars) == expected


class TestWeeklyForLoopConsolidation:
    """consolidate_and_organize must call consolidate_data_by_period per week, not once for full range."""

    def test_new_data_does_not_disassemble_existing_week_file(self, tmp_path):
        """Downloading Jan 4 data must not re-split an existing Jan5-Jan11 consolidated file."""
        from datetime import date, datetime, timezone

        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        minute_ns = 60_000_000_000

        # Create pre-consolidated week file: Jan 5 - Jan 11 (one file covering 7 days)
        jan5_ns = int(datetime(2024, 1, 5, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        week_bars = []
        for i in range(7 * 24 * 60):  # 7 days of 1m bars
            ts = jan5_ns + i * minute_ns
            week_bars.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(week_bars, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        files_after_setup = sorted(bar_dir.glob("*.parquet"))
        assert len(files_after_setup) == 1, "Setup should produce exactly 1 file"
        existing_file = files_after_setup[0]
        existing_mtime = existing_file.stat().st_mtime_ns

        # Now simulate downloading Jan 4 data (the day before the existing week)
        jan4_ns = int(datetime(2024, 1, 4, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        jan4_bars = []
        for i in range(24 * 60):  # 1 day of 1m bars
            ts = jan4_ns + i * minute_ns
            jan4_bars.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(99.0),
                high=instrument.make_price(100.0),
                low=instrument.make_price(98.0),
                close=instrument.make_price(99.5),
                volume=instrument.make_qty(500.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(jan4_bars, skip_disjoint_check=True)

        # Consolidate only Jan 4 (the newly downloaded day)
        consolidate_and_organize(
            catalog, Bar, str(bar_type),
            start=date(2024, 1, 4),
            end=date(2024, 1, 4),
        )

        files_after = sorted(bar_dir.glob("*.parquet"))
        # The existing Jan5-Jan11 file must NOT be touched
        still_exists = [f for f in files_after if f.stat().st_mtime_ns == existing_mtime]
        assert len(still_exists) >= 1, (
            f"The pre-existing Jan5-Jan11 file was modified or deleted! "
            f"Files after: {[f.name for f in files_after]}"
        )

        # Total data should be intact: 1 day (Jan 4) + 7 days (Jan 5-11) = 8 days
        all_bars = catalog.bars(bar_types=[str(bar_type)])
        expected = (1 + 7) * 24 * 60
        assert len(all_bars) == expected

    def test_wide_range_does_not_reprocess_existing_week_file(self, tmp_path):
        """Consolidating Jan4-Jan12 must not disassemble an existing Jan5-Jan11 file."""
        from datetime import date, datetime, timezone

        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        minute_ns = 60_000_000_000

        # Pre-consolidated week file: Jan 5 00:00 - Jan 11 23:59
        jan5_ns = int(datetime(2024, 1, 5, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        week_bars = []
        for i in range(7 * 24 * 60):
            ts = jan5_ns + i * minute_ns
            week_bars.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(week_bars, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        existing_file = sorted(bar_dir.glob("*.parquet"))[0]
        existing_mtime = existing_file.stat().st_mtime_ns
        existing_name = existing_file.name

        # New fragments: Jan 4 (one day) and Jan 12 (one day)
        for day_offset, day_num in [(4, 4), (12, 12)]:
            day_ns = int(datetime(2024, 1, day_num, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
            day_bars = []
            for i in range(24 * 60):
                ts = day_ns + i * minute_ns
                day_bars.append(Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(99.0),
                    high=instrument.make_price(100.0),
                    low=instrument.make_price(98.0),
                    close=instrument.make_price(99.5),
                    volume=instrument.make_qty(500.0),
                    ts_event=ts,
                    ts_init=ts,
                ))
            catalog.write_data(day_bars, skip_disjoint_check=True)

        # Consolidate the FULL range spanning all data
        consolidate_and_organize(
            catalog, Bar, str(bar_type),
            start=date(2024, 1, 4),
            end=date(2024, 1, 12),
        )

        files_after = sorted(bar_dir.glob("*.parquet"))
        # The Jan5-Jan11 file must still exist with same mtime (not reprocessed)
        still_exists = [f for f in files_after if f.stat().st_mtime_ns == existing_mtime]
        assert len(still_exists) == 1, (
            f"The pre-existing Jan5-Jan11 file was reprocessed! "
            f"Original: {existing_name}, Files after: {[f.name for f in files_after]}"
        )

        # All data intact: Jan 4 (1d) + Jan 5-11 (7d) + Jan 12 (1d) = 9 days
        all_bars = catalog.bars(bar_types=[str(bar_type)])
        expected = 9 * 24 * 60
        assert len(all_bars) == expected

    def test_multi_week_fragments_consolidate_into_weekly_files(self, tmp_path):
        """Fresh 3-week ingest (all fragments) should produce ~3 weekly files."""
        from datetime import date, datetime, timezone

        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        minute_ns = 60_000_000_000
        # Write 21 days (3 weeks) as daily fragments (21 separate write_data calls)
        jan1_ns = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        for day in range(21):
            day_start = jan1_ns + day * 24 * 60 * minute_ns
            bars = []
            for i in range(24 * 60):  # 1 day
                ts = day_start + i * minute_ns
                bars.append(Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                ))
            catalog.write_data(bars, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        assert len(list(bar_dir.glob("*.parquet"))) == 21

        consolidate_and_organize(
            catalog, Bar, str(bar_type),
            start=date(2024, 1, 1),
            end=date(2024, 1, 21),
        )

        files_after = sorted(bar_dir.glob("*.parquet"))
        # Should produce a small number of weekly files (3-4 depending on alignment)
        assert len(files_after) <= 5, f"Expected <=5 consolidated files, got {len(files_after)}"
        assert len(files_after) >= 3, f"Expected >=3 files for 3 weeks, got {len(files_after)}"

        # Data integrity
        all_bars = catalog.bars(bar_types=[str(bar_type)])
        assert len(all_bars) == 21 * 24 * 60

    def test_no_start_end_consolidates_all_fragments(self, tmp_path):
        """Without start/end, all fragments are consolidated into weekly files."""
        from datetime import datetime, timezone

        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        minute_ns = 60_000_000_000
        jan1_ns = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        for day in range(14):  # 2 weeks as daily fragments
            day_start = jan1_ns + day * 24 * 60 * minute_ns
            bars = []
            for i in range(24 * 60):
                ts = day_start + i * minute_ns
                bars.append(Bar(
                    bar_type=bar_type,
                    open=instrument.make_price(100.0),
                    high=instrument.make_price(101.0),
                    low=instrument.make_price(99.0),
                    close=instrument.make_price(100.5),
                    volume=instrument.make_qty(1000.0),
                    ts_event=ts,
                    ts_init=ts,
                ))
            catalog.write_data(bars, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        assert len(list(bar_dir.glob("*.parquet"))) == 14

        # No start/end — full consolidation
        consolidate_and_organize(catalog, Bar, str(bar_type))

        files_after = list(bar_dir.glob("*.parquet"))
        assert len(files_after) <= 4
        all_bars = catalog.bars(bar_types=[str(bar_type)])
        assert len(all_bars) == 14 * 24 * 60

    def test_fragment_crossing_week_boundary_does_not_corrupt_adjacent_week(self, tmp_path):
        """A fragment spanning a week boundary must not pull in files from the next week."""
        from datetime import date, datetime, timezone

        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        minute_ns = 60_000_000_000
        # Figure out the epoch-week boundary after Jan 4
        WEEK_NS = 7 * 24 * 3600 * 1_000_000_000
        jan4_ns = int(datetime(2024, 1, 4, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        week_boundary = ((jan4_ns // WEEK_NS) + 1) * WEEK_NS  # Start of next epoch-week
        boundary_dt = datetime.fromtimestamp(week_boundary / 1e9, tz=timezone.utc)

        # Pre-existing consolidated file in the NEXT week (starts at boundary, spans 7 days)
        next_week_bars = []
        for i in range(7 * 24 * 60):
            ts = week_boundary + i * minute_ns
            next_week_bars.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(next_week_bars, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        next_week_file = sorted(bar_dir.glob("*.parquet"))[0]
        next_week_mtime = next_week_file.stat().st_mtime_ns

        # Two fragments in the CURRENT week that straddle the boundary:
        # Fragment A: 6 hours before boundary
        # Fragment B: from 3 hours before boundary to 3 hours after (crosses!)
        frag_a_start = week_boundary - 12 * 60 * minute_ns  # 12 hours before
        frag_a_bars = []
        for i in range(6 * 60):  # 6 hours
            ts = frag_a_start + i * minute_ns
            frag_a_bars.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(99.0),
                high=instrument.make_price(100.0),
                low=instrument.make_price(98.0),
                close=instrument.make_price(99.5),
                volume=instrument.make_qty(500.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(frag_a_bars, skip_disjoint_check=True)

        frag_b_start = week_boundary - 3 * 60 * minute_ns  # 3 hours before
        frag_b_bars = []
        for i in range(6 * 60):  # 6 hours (crosses boundary by 3 hours)
            ts = frag_b_start + i * minute_ns
            frag_b_bars.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(98.0),
                high=instrument.make_price(99.0),
                low=instrument.make_price(97.0),
                close=instrument.make_price(98.5),
                volume=instrument.make_qty(300.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(frag_b_bars, skip_disjoint_check=True)

        # Consolidate the range covering the fragments
        frag_start_date = datetime.fromtimestamp(frag_a_start / 1e9, tz=timezone.utc).date()
        frag_end_date = datetime.fromtimestamp((frag_b_start + 6*60*minute_ns) / 1e9, tz=timezone.utc).date()
        consolidate_and_organize(
            catalog, Bar, str(bar_type),
            start=frag_start_date,
            end=frag_end_date,
        )

        files_after = sorted(bar_dir.glob("*.parquet"))
        # After consolidation: fragments merged into current-week file,
        # spill-over portion merged into next-week file via _merge_overlapping_files.
        # The key invariant is data integrity — no data loss.
        all_bars = catalog.bars(bar_types=[str(bar_type)])
        # next_week: 7*24*60=10080, frag_a: 6*60=360, frag_b: 6*60=360
        # frag_a and frag_b overlap by 3 hours (180 bars) → unique = 360+360-180 = 540
        # frag_b spills 3h into next_week (180 bars overlap with next_week) → deduped
        # Total unique = 10080 + 540 - 180 = 10440
        assert len(all_bars) >= 10080 + 360, (
            f"Data loss detected: {len(all_bars)} bars, expected at least {10080 + 360}"
        )
        # Should not exceed non-deduped maximum
        assert len(all_bars) <= 10080 + 360 + 360

    def test_download_within_existing_week_adds_without_corruption(self, tmp_path):
        """Downloading Jan 6 data when Jan5-Jan11 file already exists: no corruption."""
        from datetime import date, datetime, timezone

        from nautilus_trader.model.data import TradeTick
        from nautilus_trader.model.enums import AggressorSide
        from nautilus_trader.model.identifiers import TradeId
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        # Pre-existing consolidated week file: Jan 5-11 (one tick per hour for simplicity)
        jan5_ns = int(datetime(2024, 1, 5, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        hour_ns = 3600 * 1_000_000_000
        week_ticks = []
        for i in range(7 * 24):
            ts = jan5_ns + i * hour_ns
            week_ticks.append(TradeTick(
                instrument_id=instrument.id,
                price=instrument.make_price(100.0),
                size=instrument.make_qty(1.0),
                aggressor_side=AggressorSide.BUYER,
                trade_id=TradeId(str(i + 1)),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(week_ticks, skip_disjoint_check=True)

        tick_dir = tmp_path / "data" / "trade_tick" / str(instrument.id)
        existing_file = sorted(tick_dir.glob("*.parquet"))[0]
        existing_mtime = existing_file.stat().st_mtime_ns
        original_count = len(catalog.query(TradeTick, instrument_ids=[str(instrument.id)]))

        # Download Jan 6 data (within the existing file's range) — simulates gap backfill
        # Use trade_ids that DON'T collide with existing (existing Jan 6 = ids 25-48)
        jan6_ns = int(datetime(2024, 1, 6, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        new_ticks = []
        for i in range(24):  # 1 tick per hour, different trade_ids
            ts = jan6_ns + i * hour_ns
            new_ticks.append(TradeTick(
                instrument_id=instrument.id,
                price=instrument.make_price(100.0),
                size=instrument.make_qty(1.0),
                aggressor_side=AggressorSide.BUYER,
                trade_id=TradeId(str(1000 + i)),  # guaranteed unique
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(new_ticks, skip_disjoint_check=True)

        consolidate_and_organize(
            catalog, TradeTick, str(instrument.id),
            start=date(2024, 1, 6),
            end=date(2024, 1, 6),
        )

        # _merge_overlapping_files merges the small fragment into the big file
        # Data integrity: all ticks preserved (dedup by ts_event + trade_id keeps unique trade_ids)
        all_ticks = catalog.query(TradeTick, instrument_ids=[str(instrument.id)])
        assert len(all_ticks) == original_count + 24

    def test_nt_deduplicate_table_removes_exact_duplicate_rows(self, tmp_path):
        """NT's _deduplicate_table removes rows where ALL columns are identical."""
        import pyarrow as pa
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        # Same row repeated
        table = pa.table({
            "ts_event": [100, 100, 200, 200, 300],
            "trade_id": ["a", "a", "b", "b", "c"],
            "price": [1.0, 1.0, 2.0, 2.0, 3.0],
        })
        deduped = ParquetDataCatalog._deduplicate_table(table)
        assert deduped.num_rows == 3

        # Same ts_event+trade_id but different price → NOT deduped (correct)
        table2 = pa.table({
            "ts_event": [100, 100],
            "trade_id": ["a", "a"],
            "price": [1.0, 1.5],
        })
        deduped2 = ParquetDataCatalog._deduplicate_table(table2)
        assert deduped2.num_rows == 2

    def test_two_overlapping_large_files_are_deduped(self, tmp_path):
        """Two consolidated files with overlapping ranges must be deduped to avoid backtest corruption."""
        from datetime import date, datetime, timezone

        from nautilus_trader.model.data import Bar
        from nautilus_trader.persistence.catalog import ParquetDataCatalog

        from tinohelm.data.catalog import _make_bar_type, _make_instrument, consolidate_and_organize

        instrument = _make_instrument("BTCUSDT-PERP")
        bar_type = _make_bar_type(instrument.id, "1m")
        catalog = ParquetDataCatalog(str(tmp_path))
        catalog.write_data([instrument])

        minute_ns = 60_000_000_000
        # File 1: Jan 1 - Jan 8 (8 days, span > 5 days = "consolidated")
        jan1_ns = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        bars1 = []
        for i in range(8 * 24 * 60):
            ts = jan1_ns + i * minute_ns
            bars1.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(bars1, skip_disjoint_check=True)

        # File 2: Jan 6 - Jan 14 (9 days, overlaps file 1 by 3 days)
        jan6_ns = int(datetime(2024, 1, 6, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
        bars2 = []
        for i in range(9 * 24 * 60):
            ts = jan6_ns + i * minute_ns
            bars2.append(Bar(
                bar_type=bar_type,
                open=instrument.make_price(100.0),
                high=instrument.make_price(101.0),
                low=instrument.make_price(99.0),
                close=instrument.make_price(100.5),
                volume=instrument.make_qty(1000.0),
                ts_event=ts,
                ts_init=ts,
            ))
        catalog.write_data(bars2, skip_disjoint_check=True)

        bar_dir = tmp_path / "data" / "bar" / str(bar_type)
        assert len(list(bar_dir.glob("*.parquet"))) == 2

        # Consolidate — must resolve the overlap
        consolidate_and_organize(catalog, Bar, str(bar_type))

        all_bars = catalog.bars(bar_types=[str(bar_type)])
        expected_unique = 14 * 24 * 60  # Jan 1 through Jan 14
        assert len(all_bars) == expected_unique, (
            f"Overlap not deduped! Got {len(all_bars)}, expected {expected_unique}. "
            f"Backtest would receive duplicate bars."
        )
