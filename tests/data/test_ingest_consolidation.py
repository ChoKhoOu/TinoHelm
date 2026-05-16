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
    """_detect_and_backfill_gaps triggers re-download for missing date ranges."""

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
