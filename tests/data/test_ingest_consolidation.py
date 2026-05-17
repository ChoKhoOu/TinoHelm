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

        # File count: affected files (week 1 + week 2) merged into one,
        # plus untouched files remain. Total should not grow unbounded.
        # Fewer files is acceptable (merge), but data must be intact.
        assert len(files_after) >= 2, (
            "Expected at least untouched files + merged result"
        )
