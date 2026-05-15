"""Tests for ``CatalogSession`` — Catalog CRUD deep-module (see Issue #156 PR1).

CatalogSession is the single entry point for Catalog reads/writes/deletes/compaction.
Tests construct it directly against ``tmp_path`` and a local storage provider so
they stay independent of FastAPI, Redis, and Postgres.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 1. Tracer bullet — path resolution
# ---------------------------------------------------------------------------

class TestResolveCatalogPath:
    def test_none_returns_base(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        assert session.resolve_catalog_path(None) == tmp_path

    def test_klines_returns_base(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        assert session.resolve_catalog_path("klines") == tmp_path

    def test_funding_rate_returns_base(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        # fundingRate maps to a non-writable category → fallback to base path.
        assert session.resolve_catalog_path("fundingRate") == tmp_path

    def test_unknown_source_returns_base(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        assert session.resolve_catalog_path("unknown") == tmp_path


# ---------------------------------------------------------------------------
# 2. resolve_bar_catalog_path — legacy flat-layout fallback
# ---------------------------------------------------------------------------

def _write_dummy_parquet(dir_path: Path) -> Path:
    """Create an empty file with a ``.parquet`` suffix so ``iter_files`` picks it up."""
    dir_path.mkdir(parents=True, exist_ok=True)
    fake = dir_path / "fake.parquet"
    fake.write_bytes(b"")
    return fake


class TestResolveBarCatalogPath:
    def test_always_returns_catalog_root(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        resolved = session.resolve_bar_catalog_path("klines", "BTCUSDT-PERP", "5m")
        assert resolved == tmp_path

    def test_ignores_source_type(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        resolved = session.resolve_bar_catalog_path("markPriceKlines", "BTCUSDT-PERP", "5m")
        assert resolved == tmp_path


# ---------------------------------------------------------------------------
# 3. delete_storage — six data_type branches
# ---------------------------------------------------------------------------

class TestDeleteStorageUnknown:
    def test_unknown_data_type_returns_zero(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="mystery",
            interval="5m",
        )
        assert (deleted, freed) == (0, 0)


class TestDeleteStorageBar:
    def test_deletes_bar_parquet_files(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        bar_dir = tmp_path / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "5m")
        bar_dir.mkdir(parents=True)
        p1 = bar_dir / "a.parquet"
        p2 = bar_dir / "b.parquet"
        p1.write_bytes(b"x" * 100)
        p2.write_bytes(b"y" * 50)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="bar",
            interval="5m",
            source_type="klines",
        )
        assert deleted == 2
        assert freed == 150
        assert not p1.exists()
        assert not p2.exists()


class TestDeleteStorageTicks:
    def test_deletes_trade_tick_files(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = tmp_path / "data" / "trade_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        (tick_dir / "t.parquet").write_bytes(b"z" * 33)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="trade_tick",
            interval="tick",
            source_type="trades",
        )
        assert deleted == 1
        assert freed == 33

    def test_deletes_quote_tick_files(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = tmp_path / "data" / "quote_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        (tick_dir / "q.parquet").write_bytes(b"q" * 17)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="quote_tick",
            interval="tick",
            source_type="bookTicker",
        )
        assert deleted == 1
        assert freed == 17


class TestDeleteStorageSingleFileCategories:
    def test_deletes_metrics_file(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession, metrics_parquet_path

        path = metrics_parquet_path("BTCUSDT-PERP", tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"m" * 25)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="metrics",
            interval="5m",
        )
        assert deleted == 1
        assert freed == 25
        assert not path.exists()

    def test_deletes_order_book_delta_file(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession, book_depth_parquet_path

        path = book_depth_parquet_path("BTCUSDT-PERP", tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"d" * 13)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="order_book_delta",
            interval="tick",
        )
        assert deleted == 1
        assert freed == 13


class TestDeleteStorageDirectUpdates:
    def test_deletes_mark_price_update_files_recursively(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession, mark_price_update_dir

        path = mark_price_update_dir("BTCUSDT-PERP", tmp_path) / "2025" / "01" / "01" / "part.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"m" * 17)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="mark_price",
            interval="tick",
        )
        assert deleted == 1
        assert freed == 17
        assert not path.exists()

    def test_deletes_index_price_update_files_recursively(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession, index_price_update_dir

        path = index_price_update_dir("BTCUSDT-PERP", tmp_path) / "2025" / "01" / "01" / "part.parquet"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"i" * 19)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="index_price",
            interval="tick",
        )
        assert deleted == 1
        assert freed == 19
        assert not path.exists()


class TestDeleteStorageFundingRate:
    def test_deletes_parquet_and_json(self, tmp_path: Path, paths_override):
        from tinohelm.data.catalog import CatalogSession, funding_rate_parquet_path

        funding_json_dir = tmp_path / "funding_rates"
        funding_json_dir.mkdir()
        paths_override("funding_rates", funding_json_dir)

        parquet = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet.parent.mkdir(parents=True, exist_ok=True)
        parquet.write_bytes(b"p" * 40)
        json_file = funding_json_dir / "btcusdt-perp.json"
        json_file.write_bytes(b"j" * 10)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="funding_rate",
            interval="8h",
        )
        assert deleted == 2
        assert freed == 50
        assert not parquet.exists()
        assert not json_file.exists()

    def test_missing_json_still_deletes_parquet(self, tmp_path: Path, paths_override):
        from tinohelm.data.catalog import CatalogSession, funding_rate_parquet_path

        funding_json_dir = tmp_path / "funding_rates"
        funding_json_dir.mkdir()
        paths_override("funding_rates", funding_json_dir)

        parquet = funding_rate_parquet_path("ETHUSDT-PERP", tmp_path)
        parquet.parent.mkdir(parents=True, exist_ok=True)
        parquet.write_bytes(b"p" * 7)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="ETHUSDT-PERP",
            data_type="funding_rate",
            interval="8h",
        )
        assert deleted == 1
        assert freed == 7

    def test_deletes_nested_nt_update_files_recursively(self, tmp_path: Path, paths_override):
        from tinohelm.data.catalog import CatalogSession, funding_rate_parquet_path, funding_rate_update_dir

        funding_json_dir = tmp_path / "funding_rates"
        funding_json_dir.mkdir()
        paths_override("funding_rates", funding_json_dir)

        update_file = funding_rate_update_dir("BTCUSDT-PERP", tmp_path) / "2025" / "1" / "part.parquet"
        update_file.parent.mkdir(parents=True, exist_ok=True)
        update_file.write_bytes(b"u" * 11)

        parquet = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        parquet.parent.mkdir(parents=True, exist_ok=True)
        parquet.write_bytes(b"p" * 7)

        json_file = funding_json_dir / "btcusdt-perp.json"
        json_file.write_bytes(b"j" * 5)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="funding_rate",
            interval="8h",
        )
        assert deleted == 3
        assert freed == 23
        assert not update_file.exists()
        assert not parquet.exists()
        assert not json_file.exists()


# ---------------------------------------------------------------------------
# 4. compact_bars — local provider
# ---------------------------------------------------------------------------

def _write_bars_streaming(catalog_path: Path, symbol: str, interval: str, chunks: int):
    """Drop ``chunks`` separate parquet files via consecutive non-merge writes.

    Returns the total number of bars written.
    """
    from tinohelm.data.catalog import _make_bar_type, _make_instrument, write_bars
    from nautilus_trader.model.data import Bar

    inst = _make_instrument(symbol)
    bar_type = _make_bar_type(inst.id, interval)
    total = 0
    base_ns = 1_700_000_000_000_000_000
    for chunk in range(chunks):
        bars = []
        for i in range(3):
            ts_ns = base_ns + (chunk * 10 + i) * 5 * 60 * 1_000_000_000
            bars.append(Bar(
                bar_type=bar_type,
                open=inst.make_price(100.0 + i),
                high=inst.make_price(101.0 + i),
                low=inst.make_price(99.0 + i),
                close=inst.make_price(100.5 + i),
                volume=inst.make_qty(10.0),
                ts_event=ts_ns,
                ts_init=ts_ns,
            ))
        write_bars(bars, symbol, interval, catalog_path, merge=False, source_type="klines")
        total += len(bars)
    return total


class TestCompactBars:
    def test_single_file_is_noop(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        _write_bars_streaming(tmp_path, "BTCUSDT-PERP", "5m", chunks=1)
        bar_dir = (
            tmp_path
            / "data"
            / "bar"
            / make_bar_type_str("BTCUSDT-PERP", "5m")
        )
        assert len(list(bar_dir.glob("*.parquet"))) == 1

        session = CatalogSession(tmp_path)
        result = session.compact_bars("BTCUSDT-PERP", "5m")
        assert result["files_before"] == 1
        assert result["files_after"] == 1
        assert result["bars_count"] == 0
        assert result["size_before"] == result["size_after"]

    def test_multi_file_merges_to_one(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        total_bars = _write_bars_streaming(tmp_path, "BTCUSDT-PERP", "5m", chunks=3)
        bar_dir = (
            tmp_path
            / "data"
            / "bar"
            / make_bar_type_str("BTCUSDT-PERP", "5m")
        )
        assert len(list(bar_dir.glob("*.parquet"))) == 3

        session = CatalogSession(tmp_path)
        result = session.compact_bars("BTCUSDT-PERP", "5m")
        assert result["files_before"] == 3
        assert result["files_after"] == 1
        assert result["bars_count"] == total_bars
        assert result["size_after"] > 0

    def test_result_shape_is_stable(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        _write_bars_streaming(tmp_path, "BTCUSDT-PERP", "5m", chunks=2)
        session = CatalogSession(tmp_path)
        result = session.compact_bars("BTCUSDT-PERP", "5m")
        assert set(result) == {
            "files_before",
            "files_after",
            "bars_count",
            "size_before",
            "size_after",
        }


# ---------------------------------------------------------------------------
# 5. funding-rate read-side API
# ---------------------------------------------------------------------------


class TestFundingCacheCovers:
    def test_cache_spans_full_range(self, tmp_path: Path, paths_override):
        from datetime import date, datetime, timezone

        import polars as pl

        from tinohelm.data.catalog import CatalogSession, funding_rate_update_dir

        funding_dir = tmp_path / "funding_rates"
        paths_override("funding_rates", funding_dir)
        # Write parquet data that spans the range (funding_cache_covers delegates
        # to funding_parquet_covers which reads from the update dir).
        base_ns = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
        step_ns = 8 * 3600 * 1_000_000_000
        timestamps = [base_ns + i * step_ns for i in range(9)]  # up to 2024-01-03 16:00 UTC
        update_dir = funding_rate_update_dir("BTCUSDT-PERP", tmp_path) / "2024" / "1"
        update_dir.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"ts_event": timestamps}).write_parquet(update_dir / "part.parquet")

        session = CatalogSession(tmp_path)
        assert session.funding_cache_covers(
            "BTCUSDT-PERP", date(2024, 1, 1), date(2024, 1, 2)
        ) is True

    def test_cache_missing_tail(self, tmp_path: Path, paths_override):
        from datetime import date, datetime, timezone

        import polars as pl

        from tinohelm.data.catalog import CatalogSession, funding_rate_update_dir

        funding_dir = tmp_path / "funding_rates"
        paths_override("funding_rates", funding_dir)
        # Only one timestamp — does not span the requested range.
        base_ns = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
        update_dir = funding_rate_update_dir("BTCUSDT-PERP", tmp_path) / "2024" / "1"
        update_dir.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"ts_event": [base_ns]}).write_parquet(update_dir / "part.parquet")

        session = CatalogSession(tmp_path)
        assert session.funding_cache_covers(
            "BTCUSDT-PERP", date(2024, 1, 1), date(2024, 1, 5)
        ) is False

    def test_no_cache_returns_false(self, tmp_path: Path, paths_override):
        from datetime import date

        from tinohelm.data.catalog import CatalogSession

        paths_override("funding_rates", tmp_path / "funding_rates")
        session = CatalogSession(tmp_path)
        assert session.funding_cache_covers(
            "SOLUSDT-PERP", date(2024, 1, 1), date(2024, 1, 2)
        ) is False




class TestAggregateParquetStats:
    def test_empty_dir_returns_none(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        stats = session.aggregate_parquet_stats(
            symbol="BTCUSDT-PERP",
            data_type="bar",
            interval="5m",
            source_type="klines",
        )
        assert stats is None

    def test_funding_rate_scoped_to_single_symbol(self, tmp_path: Path):
        """metrics / order_book_delta / funding_rate share a parent dir — the session
        must only count the requested symbol's parquet.
        """
        from tinohelm.data.catalog import CatalogSession, funding_rate_parquet_path

        btc = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        eth = funding_rate_parquet_path("ETHUSDT-PERP", tmp_path)
        btc.parent.mkdir(parents=True, exist_ok=True)
        btc.write_bytes(b"b" * 10)
        eth.write_bytes(b"e" * 99)  # should not leak into BTC stats

        session = CatalogSession(tmp_path)
        stats = session.aggregate_parquet_stats(
            symbol="BTCUSDT-PERP",
            data_type="funding_rate",
            interval="8h",
        )
        assert stats is not None
        assert stats["size_bytes"] == 10  # not 109

    def test_matches_routes_helper_for_local_bars(self, tmp_path: Path):
        """Session aggregate_parquet_stats must equal the module-level helper."""
        from tinohelm.data.catalog import (
            CatalogSession,
            _aggregate_parquet_object_stats,
        )
        from tinohelm.data.storage import get_catalog_storage
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        _write_bars_streaming(tmp_path, "BTCUSDT-PERP", "5m", chunks=2)
        bar_dir = (
            tmp_path
            / "data"
            / "bar"
            / make_bar_type_str("BTCUSDT-PERP", "5m")
        )
        storage = get_catalog_storage(catalog_root=tmp_path)
        objects = list(storage.iter_files(bar_dir, suffix=".parquet", recursive=False))
        expected = _aggregate_parquet_object_stats(objects, storage)
        assert expected is not None

        session = CatalogSession(tmp_path)
        got = session.aggregate_parquet_stats(
            symbol="BTCUSDT-PERP",
            data_type="bar",
            interval="5m",
            source_type="klines",
        )
        assert got == expected

    @pytest.mark.parametrize(
        ("data_type", "helper_name", "source_type"),
        [
            ("mark_price", "mark_price_update_dir", "markPriceKlines"),
            ("index_price", "index_price_update_dir", "indexPriceKlines"),
        ],
    )
    def test_direct_update_stats_scan_nested_update_dirs(
        self,
        tmp_path: Path,
        data_type: str,
        helper_name: str,
        source_type: str,
    ):
        import polars as pl
        from tinohelm.data.catalog import (
            CatalogSession,
            _aggregate_parquet_object_stats,
            index_price_update_dir,
            mark_price_update_dir,
        )
        from tinohelm.data.storage import get_catalog_storage

        helper = {
            "mark_price_update_dir": mark_price_update_dir,
            "index_price_update_dir": index_price_update_dir,
        }[helper_name]
        root_dir = helper("BTCUSDT-PERP", tmp_path)
        target_dir = root_dir / "2025" / "01" / "01"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / "part.parquet"
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000], "price": [100.0]}).write_parquet(file_path)

        storage = get_catalog_storage(catalog_root=tmp_path)
        expected = _aggregate_parquet_object_stats(
            list(storage.iter_files(root_dir, suffix=".parquet", recursive=True)),
            storage,
        )
        assert expected is not None

        session = CatalogSession(tmp_path)
        got = session.aggregate_parquet_stats(
            symbol="BTCUSDT-PERP",
            data_type=data_type,
            interval="tick",
            source_type=source_type,
        )
        assert got == expected


class TestParquetSizeFor:
    """Covers the bar-only ``session.parquet_size_for`` added for PR2 —
    replaces the route-level ``_parquet_size_for`` helper.
    """

    def test_returns_zero_when_dir_missing(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        assert session.parquet_size_for("BTCUSDT-PERP", "5m") == 0

    def test_sums_parquet_files(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        bar_dir = tmp_path / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "5m")
        bar_dir.mkdir(parents=True)
        (bar_dir / "a.parquet").write_bytes(b"x" * 100)
        (bar_dir / "b.parquet").write_bytes(b"y" * 200)
        # Non-parquet files must be ignored.
        (bar_dir / "ignore.txt").write_bytes(b"z" * 1000)

        session = CatalogSession(tmp_path)
        assert session.parquet_size_for("BTCUSDT-PERP", "5m") == 300

    def test_invalid_interval_propagates_value_error(self, tmp_path: Path):
        import pytest

        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        with pytest.raises(ValueError):
            session.parquet_size_for("BTCUSDT-PERP", "5x")


class TestScanBars:
    """Covers ``session.scan_bars`` (PR2) — replaces the in-route aggregation
    over ``data/bar/<bar_type>`` dirs. The DB upsert lives in the route."""

    def test_empty_catalog_returns_no_entries(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        result = session.scan_bars()
        assert result.entries == []
        assert result.scanned == 0

    def test_source_aware_layout_produces_one_entry_per_symbol_interval_source(
        self, tmp_path: Path
    ):
        import polars as pl

        from tinohelm.data.catalog import CatalogSession
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        symbol = "BTCUSDT-PERP"
        nt_sym = normalize_symbol(symbol)
        source_root = resolve_catalog_path(tmp_path, "klines")
        bar_dir = source_root / "data" / "bar" / f"{nt_sym}-5-MINUTE-LAST-EXTERNAL"
        bar_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(
            bar_dir / "a.parquet"
        )

        session = CatalogSession(tmp_path)
        result = session.scan_bars()

        assert result.scanned == 1
        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.symbol == symbol
        assert entry.data_type == "bar"
        assert entry.interval == "5m"
        assert entry.source_type == "klines"
        assert entry.record_count == 1
        assert entry.file_path == str(source_root)


class TestScanTicks:
    def test_empty_catalog_returns_no_entries(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        result = session.scan_ticks()
        assert result.entries == []
        assert result.scanned == 0

    def test_book_ticker_produces_quote_tick_entry(self, tmp_path: Path):
        import polars as pl

        from tinohelm.data.catalog import CatalogSession
        from tinohelm.data.catalog_helpers import resolve_catalog_path
        from tinohelm.strategy.loader import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        source_root = resolve_catalog_path(tmp_path, "bookTicker")
        quote_dir = source_root / "data" / "quote_tick" / nt_sym
        quote_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(
            quote_dir / "quotes.parquet"
        )

        session = CatalogSession(tmp_path)
        result = session.scan_ticks()

        assert result.scanned == 1
        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.symbol == "BTCUSDT-PERP"
        assert entry.data_type == "quote_tick"
        assert entry.interval == "tick"
        assert entry.source_type == "bookTicker"
        assert entry.file_path == str(source_root)


class TestMergedBarStats:
    """``merged_bar_stats`` scans bar stats from the single NT-native catalog
    root — needed by the compact background task so it doesn't overwrite the
    DB row's ``size_bytes`` / ``record_count`` when compaction happens.
    """

    def test_returns_none_when_no_parquet_anywhere(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        assert (
            session.merged_bar_stats("BTCUSDT-PERP", "5m", source_type="klines")
            is None
        )

    def test_counts_all_files_in_single_root(self, tmp_path: Path):
        import polars as pl

        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        nt_sub = make_bar_type_str("BTCUSDT-PERP", "5m")
        bar_dir = tmp_path / "data" / "bar" / nt_sub
        bar_dir.mkdir(parents=True)
        pl.DataFrame(
            {"ts_event": [1_735_689_600_000_000_000, 1_735_776_000_000_000_000]}
        ).write_parquet(bar_dir / "a.parquet")
        pl.DataFrame(
            {
                "ts_event": [
                    1_735_862_400_000_000_000,
                    1_735_948_800_000_000_000,
                    1_736_035_200_000_000_000,
                ]
            }
        ).write_parquet(bar_dir / "b.parquet")

        session = CatalogSession(tmp_path)
        stats = session.merged_bar_stats("BTCUSDT-PERP", "5m", source_type="klines")
        assert stats is not None
        assert stats["record_count"] == 5
        expected_size = (bar_dir / "a.parquet").stat().st_size + (
            bar_dir / "b.parquet"
        ).stat().st_size
        assert stats["size_bytes"] == expected_size


class TestScanSingleFiles:
    """``scan_single_files`` covers the per-symbol Parquet categories that share
    a parent dir: funding_rate, metrics, order_book_delta. These never got
    scanned by the pre-PR2 route loop either, but the session is the right
    place to add the missing discovery — single source of scan truth.
    """

    def test_empty_catalog_returns_no_entries(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        result = session.scan_single_files()
        assert result.entries == []
        assert result.scanned == 0

    def test_funding_rate_parquet_produces_entry_per_symbol(self, tmp_path: Path):
        import polars as pl

        from tinohelm.data.catalog import CatalogSession, funding_rate_parquet_path

        for symbol in ("BTCUSDT-PERP", "ETHUSDT-PERP"):
            path = funding_rate_parquet_path(symbol, tmp_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(path)

        session = CatalogSession(tmp_path)
        result = session.scan_single_files()

        assert result.scanned == 2
        entries = {entry.symbol: entry for entry in result.entries}
        assert set(entries) == {"BTCUSDT-PERP", "ETHUSDT-PERP"}
        for entry in entries.values():
            assert entry.data_type == "funding_rate"
            assert entry.interval == "8h"
            assert entry.source_type == "fundingRate"
            assert entry.record_count == 1

    def test_metrics_parquet_produces_entry(self, tmp_path: Path):
        import polars as pl

        from tinohelm.data.catalog import CatalogSession, metrics_parquet_path

        path = metrics_parquet_path("BTCUSDT-PERP", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(path)

        session = CatalogSession(tmp_path)
        result = session.scan_single_files()

        metrics_entries = [e for e in result.entries if e.data_type == "metrics"]
        assert len(metrics_entries) == 1
        entry = metrics_entries[0]
        assert entry.symbol == "BTCUSDT-PERP"
        assert entry.source_type == "metrics"
        assert entry.interval == "tick"
        assert entry.record_count == 1

    def test_order_book_delta_parquet_produces_entry(self, tmp_path: Path):
        import polars as pl

        from tinohelm.data.catalog import CatalogSession, book_depth_parquet_path

        path = book_depth_parquet_path("BTCUSDT-PERP", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"ts_event": [1_735_689_600_000_000_000]}).write_parquet(path)

        session = CatalogSession(tmp_path)
        result = session.scan_single_files()

        obd_entries = [e for e in result.entries if e.data_type == "order_book_delta"]
        assert len(obd_entries) == 1
        entry = obd_entries[0]
        assert entry.symbol == "BTCUSDT-PERP"
        assert entry.source_type == "bookDepth"
        assert entry.interval == "tick"


class TestFundingParquetCovers:
    def test_returns_false_when_parquet_missing(self, tmp_path: Path):
        from datetime import date

        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        assert session.funding_parquet_covers(
            "BTCUSDT-PERP", date(2024, 1, 1), date(2024, 1, 2)
        ) is False

    def test_returns_true_when_parquet_spans_range(self, tmp_path: Path):
        from datetime import date, datetime, timezone

        import pyarrow as pa
        import pyarrow.parquet as pq

        from tinohelm.data.catalog import (
            CatalogSession,
            funding_rate_parquet_path,
        )

        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        base_ms = int(base.timestamp() * 1000)
        ts_events = []
        rates = []
        for i in range(5):
            ms = base_ms + i * 8 * 3600 * 1000
            ns = ms * 1_000_000
            ts_events.append(ns)
            rates.append(0.0001 * (i + 1))

        path = funding_rate_parquet_path("BTCUSDT-PERP", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        table = pa.table({"ts_event": pa.array(ts_events, type=pa.int64()), "funding_rate": pa.array(rates, type=pa.float64())})
        pq.write_table(table, str(path))

        session = CatalogSession(tmp_path)
        assert session.funding_parquet_covers(
            "BTCUSDT-PERP", date(2024, 1, 1), date(2024, 1, 2)
        ) is True

    def test_returns_true_when_nt_update_dir_spans_range(self, tmp_path: Path):
        from datetime import date

        import polars as pl

        from tinohelm.data.catalog import CatalogSession, funding_rate_update_dir

        update_dir = funding_rate_update_dir("BTCUSDT-PERP", tmp_path) / "2024" / "1"
        update_dir.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(
            {"ts_event": [1_704_067_200_000_000_000, 1_704_758_400_000_000_000]}
        ).write_parquet(update_dir / "part.parquet")

        session = CatalogSession(tmp_path)
        assert session.funding_parquet_covers(
            "BTCUSDT-PERP", date(2024, 1, 1), date(2024, 1, 2)
        ) is True

    def test_aggregate_stats_reads_nt_update_dir_for_funding_rate(self, tmp_path: Path):
        import polars as pl

        from tinohelm.data.catalog import CatalogSession, funding_rate_update_dir

        update_dir = funding_rate_update_dir("BTCUSDT-PERP", tmp_path) / "2024" / "1"
        update_dir.mkdir(parents=True, exist_ok=True)
        pl.DataFrame({"ts_event": [1_704_067_200_000_000_000, 1_704_758_400_000_000_000]}).write_parquet(
            update_dir / "part.parquet"
        )

        session = CatalogSession(tmp_path)
        stats = session.aggregate_parquet_stats(
            symbol="BTCUSDT-PERP",
            data_type="funding_rate",
            interval="8h",
            source_type="fundingRate",
        )

        assert stats is not None
        assert stats["record_count"] == 2
        assert stats["size_bytes"] > 0


# ---------------------------------------------------------------------------
# 9. compact_bars — remote provider
# ---------------------------------------------------------------------------


class _FakeRemoteFS:
    """In-memory filesystem for remote storage tests."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects: dict[str, bytes] = dict(objects or {})
        self.rm_calls: list[str] = []

    def open(self, path: str, mode: str = "rb"):
        import io

        assert mode == "rb"
        return io.BytesIO(self.objects[path])

    def put_file(self, local_path: str, remote_path: str) -> None:
        self.objects[remote_path] = Path(local_path).read_bytes()

    def rm(self, path: str) -> None:
        self.rm_calls.append(path)
        if path in self.objects:
            del self.objects[path]


class _FakeRemoteStorage:
    """Minimal remote CatalogStorageProvider backed by _FakeRemoteFS.

    Keys stored in the FS use the pattern ``bucket/catalog/{rel_path}`` where
    ``rel_path`` is relative to ``self.catalog_root``.
    """

    provider = "s3"

    def __init__(self, root: Path, fs: _FakeRemoteFS):
        self.catalog_root = root
        self._fs = fs
        self.fs_storage_options = {"endpoint_url": "https://example.com"}
        self.fs_rust_storage_options = {"endpoint_url": "https://example.com"}

    def _to_key(self, path: Path | str) -> str:
        try:
            rel = Path(path).relative_to(self.catalog_root).as_posix()
        except ValueError:
            rel = Path(path).as_posix().lstrip("/")
        return f"bucket/catalog/{rel}" if rel != "." else "bucket/catalog"

    def uri_for_catalog_root(self, catalog_root: Path | str | None = None) -> str:
        if catalog_root is None:
            return "s3://bucket/catalog"
        try:
            rel = Path(catalog_root).relative_to(self.catalog_root).as_posix()
        except ValueError:
            rel = ""
        return f"s3://bucket/catalog/{rel}" if rel and rel != "." else "s3://bucket/catalog"

    def iter_files(self, prefix: Path | str, suffix: str = "", recursive: bool = True):
        prefix_key = self._to_key(prefix)
        for key, payload in sorted(self._fs.objects.items()):
            if not key.startswith(prefix_key + "/"):
                continue
            remainder = key[len(prefix_key) + 1:]
            if not recursive and "/" in remainder:
                continue
            if suffix and not key.endswith(suffix):
                continue
            obj = type("Obj", (), {})()
            obj.key = key
            obj.path = self.catalog_root / key.removeprefix("bucket/catalog/")
            obj.size = len(payload)
            obj.last_modified = None
            yield obj

    def exists(self, path: Path | str) -> bool:
        key = self._to_key(path)
        return any(k == key or k.startswith(key + "/") for k in self._fs.objects)

    def open_input_file(self, path_or_object):
        import io

        key = getattr(path_or_object, "key", None)
        if key is None:
            key = self._to_key(getattr(path_or_object, "path", path_or_object))
        return io.BytesIO(self._fs.objects[key])

    def copy_path(self, source: Path | str, dest: Path | str) -> str:
        src_key = getattr(source, "key", None) or self._to_key(source)
        dst_key = self._to_key(dest)
        self._fs.objects[dst_key] = self._fs.objects[src_key]
        return f"s3://{dst_key}"

    def delete_path(self, path: Path | str) -> None:
        key = getattr(path, "key", None) or self._to_key(path)
        self._fs.rm(key)


class TestCompactBarsRemote:
    def test_remote_compact_multi_file_merges(self, tmp_path: Path, monkeypatch):
        from types import SimpleNamespace

        from tinohelm.data.catalog import CatalogSession

        bar_type_str = "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"
        catalog_root = tmp_path

        old_keys = [
            f"bucket/catalog/data/bar/{bar_type_str}/a.parquet",
            f"bucket/catalog/data/bar/{bar_type_str}/b.parquet",
            f"bucket/catalog/data/bar/{bar_type_str}/c.parquet",
        ]
        fs = _FakeRemoteFS({k: b"old" for k in old_keys})
        storage = _FakeRemoteStorage(catalog_root, fs)

        instrument = SimpleNamespace(id="BTCUSDT-PERP.BINANCE")

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        bar_type = FakeBarType()
        bars = [SimpleNamespace(ts_event=i) for i in range(9)]
        compacted_name = "merged.parquet"

        class FakeCatalog:
            def __init__(self, catalog_path=None, **kwargs):
                self.catalog_path = catalog_path

            def bars(self, bar_types):
                return list(bars)

            def write_data(self, data, **kwargs):
                if data and hasattr(data[0], "ts_event"):
                    rel = Path(str(self.catalog_path)).relative_to(catalog_root).as_posix()
                    key = f"bucket/catalog/{rel}/data/bar/{bar_type_str}/{compacted_name}"
                    fs.objects[key] = b"compacted"

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda symbol: instrument)
        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda inst_id, interval: bar_type)
        monkeypatch.setattr(
            "tinohelm.data.catalog._catalog_for_root",
            lambda root, storage=None: FakeCatalog(catalog_path=root),
        )

        session = CatalogSession(catalog_root, storage=storage)
        result = session.compact_bars("BTCUSDT-PERP", "5m")

        assert result["files_before"] == 3
        assert result["files_after"] == 1
        assert result["bars_count"] == 9
        assert result["size_before"] == len(b"old") * 3
        assert result["size_after"] == len(b"compacted")
        assert set(result) == {"files_before", "files_after", "bars_count", "size_before", "size_after"}

    def test_remote_compact_single_file_noop(self, tmp_path: Path, monkeypatch):
        from types import SimpleNamespace

        from tinohelm.data.catalog import CatalogSession

        bar_type_str = "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"
        catalog_root = tmp_path

        fs = _FakeRemoteFS({
            f"bucket/catalog/data/bar/{bar_type_str}/only.parquet": b"single",
        })
        storage = _FakeRemoteStorage(catalog_root, fs)

        monkeypatch.setattr("tinohelm.data.catalog._make_instrument", lambda s: SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))

        class FakeBarType:
            def __str__(self):
                return bar_type_str

        monkeypatch.setattr("tinohelm.data.catalog._make_bar_type", lambda inst_id, interval: FakeBarType())

        session = CatalogSession(catalog_root, storage=storage)
        result = session.compact_bars("BTCUSDT-PERP", "5m")

        assert result["files_before"] == 1
        assert result["files_after"] == 1
        assert result["bars_count"] == 0
        assert result["size_before"] == len(b"single")
        assert result["size_after"] == len(b"single")
