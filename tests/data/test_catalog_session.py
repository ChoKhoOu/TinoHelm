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

    def test_klines_returns_bar_category(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        session = CatalogSession(tmp_path)
        assert session.resolve_catalog_path("klines") == tmp_path / "bar" / "klines"

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
    def test_returns_resolved_path_when_new_layout_files_exist(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        # New layout: files under base/bar/klines/data/bar/<bar_type>/
        bar_dir = (
            tmp_path
            / "bar"
            / "klines"
            / "data"
            / "bar"
            / make_bar_type_str("BTCUSDT-PERP", "5m")
        )
        _write_dummy_parquet(bar_dir)

        session = CatalogSession(tmp_path)
        resolved = session.resolve_bar_catalog_path("klines", "BTCUSDT-PERP", "5m")
        assert resolved == tmp_path / "bar" / "klines"

    def test_falls_back_to_base_when_only_legacy_files_exist(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        # Legacy layout: files directly under base/data/bar/<bar_type>/
        legacy_dir = tmp_path / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "5m")
        _write_dummy_parquet(legacy_dir)

        session = CatalogSession(tmp_path)
        resolved = session.resolve_bar_catalog_path("klines", "BTCUSDT-PERP", "5m")
        assert resolved == tmp_path

    def test_no_fallback_for_non_default_source(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        # Only legacy files present, but source_type is markPriceKlines —
        # fallback only applies to the legacy default source (klines).
        legacy_dir = tmp_path / "data" / "bar" / make_bar_type_str("BTCUSDT-PERP", "5m")
        _write_dummy_parquet(legacy_dir)

        session = CatalogSession(tmp_path)
        resolved = session.resolve_bar_catalog_path(
            "markPriceKlines", "BTCUSDT-PERP", "5m"
        )
        assert resolved == tmp_path / "bar" / "markPriceKlines"


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

        bar_dir = (
            tmp_path
            / "bar"
            / "klines"
            / "data"
            / "bar"
            / make_bar_type_str("BTCUSDT-PERP", "5m")
        )
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

    def test_legacy_default_source_deletes_both_roots(self, tmp_path: Path):
        """source_type=='klines' (the legacy default) scans resolved+base paths."""
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        bar_type = make_bar_type_str("BTCUSDT-PERP", "5m")
        new_dir = tmp_path / "bar" / "klines" / "data" / "bar" / bar_type
        legacy_dir = tmp_path / "data" / "bar" / bar_type
        for d in (new_dir, legacy_dir):
            d.mkdir(parents=True)
            (d / "a.parquet").write_bytes(b"x" * 20)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="bar",
            interval="5m",
            source_type="klines",
        )
        assert deleted == 2
        assert freed == 40


class TestDeleteStorageTicks:
    def test_deletes_trade_tick_files(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = tmp_path / "ticks" / "aggTrades" / "data" / "trade_tick" / nt_sym
        tick_dir.mkdir(parents=True)
        (tick_dir / "t.parquet").write_bytes(b"z" * 33)

        session = CatalogSession(tmp_path)
        deleted, freed = session.delete_storage(
            symbol="BTCUSDT-PERP",
            data_type="trade_tick",
            interval="tick",
            source_type="aggTrades",
        )
        assert deleted == 1
        assert freed == 33

    def test_deletes_quote_tick_files(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.strategy.loader_helpers import normalize_symbol

        nt_sym = normalize_symbol("BTCUSDT-PERP")
        tick_dir = tmp_path / "quotes" / "bookTicker" / "data" / "quote_tick" / nt_sym
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
            / "bar"
            / "klines"
            / "data"
            / "bar"
            / make_bar_type_str("BTCUSDT-PERP", "5m")
        )
        assert len(list(bar_dir.glob("*.parquet"))) == 1

        session = CatalogSession(tmp_path / "bar" / "klines")
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
            / "bar"
            / "klines"
            / "data"
            / "bar"
            / make_bar_type_str("BTCUSDT-PERP", "5m")
        )
        assert len(list(bar_dir.glob("*.parquet"))) == 3

        session = CatalogSession(tmp_path / "bar" / "klines")
        result = session.compact_bars("BTCUSDT-PERP", "5m")
        assert result["files_before"] == 3
        assert result["files_after"] == 1
        assert result["bars_count"] == total_bars
        assert result["size_after"] > 0

    def test_result_shape_is_stable(self, tmp_path: Path):
        from tinohelm.data.catalog import CatalogSession

        _write_bars_streaming(tmp_path, "BTCUSDT-PERP", "5m", chunks=2)
        session = CatalogSession(tmp_path / "bar" / "klines")
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

def _write_funding_json(dir_path: Path, symbol: str, times_ms: list[int]):
    import json

    dir_path.mkdir(parents=True, exist_ok=True)
    records = [
        {"funding_time_ms": t, "funding_rate": 0.0001 * i, "mark_price": 0}
        for i, t in enumerate(times_ms)
    ]
    (dir_path / f"{symbol.lower()}.json").write_text(json.dumps(records))


class TestLoadFundingRates:
    def test_returns_records_in_range(self, tmp_path: Path, paths_override):
        from datetime import datetime, timezone

        from tinohelm.data.catalog import CatalogSession

        funding_dir = tmp_path / "funding_rates"
        paths_override("funding_rates", funding_dir)
        # 2024-01-01 00:00 UTC + 8h steps.
        base_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        step_ms = 8 * 3600 * 1000
        times = [base_ms + i * step_ms for i in range(6)]
        _write_funding_json(funding_dir, "BTCUSDT-PERP", times)

        session = CatalogSession(tmp_path)
        result = session.load_funding_rates(
            "BTCUSDT-PERP",
            datetime(2024, 1, 1, 8, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 0, tzinfo=timezone.utc),
        )
        assert [r["funding_time_ms"] for r in result] == times[1:4]

    def test_missing_symbol_returns_empty(self, tmp_path: Path, paths_override):
        from datetime import datetime, timezone

        from tinohelm.data.catalog import CatalogSession

        paths_override("funding_rates", tmp_path / "funding_rates")
        session = CatalogSession(tmp_path)
        assert session.load_funding_rates(
            "SOLUSDT-PERP",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        ) == []


class TestFundingCacheCovers:
    def test_cache_spans_full_range(self, tmp_path: Path, paths_override):
        from datetime import date, datetime, timezone

        from tinohelm.data.catalog import CatalogSession

        funding_dir = tmp_path / "funding_rates"
        paths_override("funding_rates", funding_dir)
        # Coverage is judged against end-of-day on ``end``; fit that into the
        # cached timestamps by extending past the end date.
        base_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        step_ms = 8 * 3600 * 1000
        times = [base_ms + i * step_ms for i in range(9)]  # up to 2024-01-03 16:00 UTC
        _write_funding_json(funding_dir, "BTCUSDT-PERP", times)

        session = CatalogSession(tmp_path)
        assert session.funding_cache_covers(
            "BTCUSDT-PERP", date(2024, 1, 1), date(2024, 1, 2)
        ) is True

    def test_cache_missing_tail(self, tmp_path: Path, paths_override):
        from datetime import date, datetime, timezone

        from tinohelm.data.catalog import CatalogSession

        funding_dir = tmp_path / "funding_rates"
        paths_override("funding_rates", funding_dir)
        base_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        _write_funding_json(funding_dir, "BTCUSDT-PERP", [base_ms])

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


class TestFundingRateTxn:
    def _make_records(self, n: int = 3):
        from tinohelm.data.converters.funding_rate import BinanceFundingRate

        records = []
        base_ms = 1_700_000_000_000
        for i in range(n):
            ms = base_ms + i * 8 * 3600 * 1000
            ns = ms * 1_000_000
            records.append(BinanceFundingRate(
                symbol="BTCUSDT-PERP",
                funding_rate=0.0001 * (i + 1),
                funding_time_ms=ms,
                ts_event=ns,
                ts_init=ns,
            ))
        return records

    def test_happy_path_merges_into_existing_json(self, tmp_path: Path, paths_override):
        import json

        from tinohelm.data.catalog import CatalogSession

        funding_dir = tmp_path / "funding_rates"
        funding_dir.mkdir()
        paths_override("funding_rates", funding_dir)
        # Seed JSON with one old record.
        old_ms = 1_699_000_000_000
        old_record = {"funding_time_ms": old_ms, "funding_rate": 0.00005, "mark_price": 0}
        (funding_dir / "btcusdt-perp.json").write_text(json.dumps([old_record]))

        session = CatalogSession(tmp_path)
        with session.funding_rate_transaction("BTCUSDT-PERP") as txn:
            txn.write_parquet(self._make_records(3))
            txn.flush_json()

        saved = json.loads((funding_dir / "btcusdt-perp.json").read_text())
        saved_ts = [r["funding_time_ms"] for r in saved]
        assert old_ms in saved_ts
        assert len(saved) == 4
        assert sorted(saved_ts) == saved_ts

    def test_failure_restores_json_snapshot(self, tmp_path: Path, paths_override):
        import json

        from tinohelm.data.catalog import CatalogSession

        funding_dir = tmp_path / "funding_rates"
        funding_dir.mkdir()
        paths_override("funding_rates", funding_dir)
        old_payload = [{"funding_time_ms": 1_699_000_000_000, "funding_rate": 0.00005, "mark_price": 0}]
        json_file = funding_dir / "btcusdt-perp.json"
        json_file.write_text(json.dumps(old_payload))

        session = CatalogSession(tmp_path)
        with pytest.raises(RuntimeError):
            with session.funding_rate_transaction("BTCUSDT-PERP") as txn:
                txn.write_parquet(self._make_records(3))
                raise RuntimeError("simulated DB failure")

        # JSON was not flushed and the snapshot is restored unchanged.
        assert json.loads(json_file.read_text()) == old_payload

    def test_failure_on_new_symbol_leaves_no_json(self, tmp_path: Path, paths_override):
        from tinohelm.data.catalog import CatalogSession

        funding_dir = tmp_path / "funding_rates"
        funding_dir.mkdir()
        paths_override("funding_rates", funding_dir)

        session = CatalogSession(tmp_path)
        with pytest.raises(RuntimeError):
            with session.funding_rate_transaction("SOLUSDT-PERP") as txn:
                txn.write_parquet(self._make_records(3))
                raise RuntimeError("simulated DB failure")

        assert not (funding_dir / "solusdt-perp.json").exists()

    def test_unflushed_normal_exit_leaves_json_untouched(
        self, tmp_path: Path, paths_override, caplog
    ):
        """Leaving the ``with`` block without flushing should NOT update the JSON.

        The contract: ``flush_json`` must be called explicitly after the DB
        commit. Forgetting to flush leaves the parquet written but the JSON
        read-side stale; the txn logs a warning so the mistake is visible.
        """
        import json
        import logging

        from tinohelm.data.catalog import CatalogSession

        funding_dir = tmp_path / "funding_rates"
        funding_dir.mkdir()
        paths_override("funding_rates", funding_dir)
        original = [{"funding_time_ms": 1_699_000_000_000, "funding_rate": 0.00005, "mark_price": 0}]
        json_file = funding_dir / "btcusdt-perp.json"
        json_file.write_text(json.dumps(original))

        caplog.set_level(logging.WARNING)
        session = CatalogSession(tmp_path)
        with session.funding_rate_transaction("BTCUSDT-PERP") as txn:
            txn.write_parquet(self._make_records(2))
            # Note: no flush_json call.

        assert json.loads(json_file.read_text()) == original
        assert any("flush" in rec.message.lower() for rec in caplog.records)


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

    def test_matches_routes_helper_for_local_bars(self, tmp_path: Path):
        """Session aggregate_parquet_stats must equal the route-level helper."""
        from tinohelm.api.routes.data import _aggregate_parquet_object_stats
        from tinohelm.data.catalog import CatalogSession
        from tinohelm.data.storage import get_catalog_storage
        from tinohelm.strategy.loader_helpers import make_bar_type_str

        _write_bars_streaming(tmp_path, "BTCUSDT-PERP", "5m", chunks=2)
        bar_dir = (
            tmp_path
            / "bar"
            / "klines"
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

        from tinohelm.data.catalog import (
            CatalogSession,
            write_funding_rate_parquet,
        )
        from tinohelm.data.converters.funding_rate import BinanceFundingRate

        base = datetime(2024, 1, 1, tzinfo=timezone.utc)
        base_ms = int(base.timestamp() * 1000)
        records = []
        for i in range(5):
            ms = base_ms + i * 8 * 3600 * 1000
            ns = ms * 1_000_000
            records.append(BinanceFundingRate(
                symbol="BTCUSDT-PERP",
                funding_rate=0.0001 * (i + 1),
                funding_time_ms=ms,
                ts_event=ns,
                ts_init=ns,
            ))
        write_funding_rate_parquet(records, "BTCUSDT-PERP", tmp_path)

        session = CatalogSession(tmp_path)
        assert session.funding_parquet_covers(
            "BTCUSDT-PERP", date(2024, 1, 1), date(2024, 1, 2)
        ) is True
