"""Tests for tinohelm.data.pipeline.BinanceVisionPipeline.

All network, DB, and filesystem side-effects are mocked.
"""
from __future__ import annotations

import asyncio
import json
from io import BytesIO
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from tinohelm.data.downloader import VisionCsvPayload
from tinohelm.data.pipeline import (
    BinanceVisionPipeline,
    IngestResult,
    _ns_to_utc_date,
)


def _csv_payload(name: str, content: bytes) -> VisionCsvPayload:
    file_obj = BytesIO()
    file_obj.write(content)
    file_obj.seek(0)
    return VisionCsvPayload(name=name, file=file_obj)


def test_ns_to_utc_date_uses_integer_seconds_at_day_boundary() -> None:
    ns = 1_737_417_599_999_999_999

    assert _ns_to_utc_date(ns) == date(2025, 1, 20)


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
        )
        assert r.objects_count == 1234
        assert r.files_written == 3
        assert r.skipped is True



class TestDirectUpdateWrites:
    def test_write_objects_uses_nt_direct_updates_for_mark_price(self, tmp_path: Path, monkeypatch):
        from nautilus_trader.model.data import MarkPriceUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from nautilus_trader.model.objects import Price

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        captured = []

        class FakeCatalog:
            def __init__(self, *_args, **_kwargs):
                pass

            def write_data(self, data, *args, **kwargs):
                captured.append((list(data), kwargs))

        monkeypatch.setattr("tinohelm.data.catalog._catalog_for_root", lambda *_args, **_kwargs: FakeCatalog())
        monkeypatch.setattr("tinohelm.data.catalog.ensure_catalog_dirs", lambda path: path)
        monkeypatch.setattr("tinohelm.data.catalog._iter_catalog_files", lambda *_args, **_kwargs: [tmp_path / "data" / "mark_price_update" / "BTCUSDT-PERP.BINANCE" / "part.parquet"])

        records = [MarkPriceUpdate(InstrumentId.from_str("BTCUSDT-PERP.BINANCE"), Price.from_str("1.0"), 1, 1)]
        paths = p._write_objects(records, "BTCUSDT-PERP", "markPriceKlines", "1m", merge=False)

        assert paths
        assert len(captured) == 1
        assert isinstance(captured[0][0][0], MarkPriceUpdate)

    def test_write_objects_uses_nt_direct_updates_for_index_price(self, tmp_path: Path, monkeypatch):
        from nautilus_trader.model.data import IndexPriceUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from nautilus_trader.model.objects import Price

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        captured = []

        class FakeCatalog:
            def __init__(self, *_args, **_kwargs):
                pass

            def write_data(self, data, *args, **kwargs):
                captured.append((list(data), kwargs))

        monkeypatch.setattr("tinohelm.data.catalog._catalog_for_root", lambda *_args, **_kwargs: FakeCatalog())
        monkeypatch.setattr("tinohelm.data.catalog.ensure_catalog_dirs", lambda path: path)
        monkeypatch.setattr("tinohelm.data.catalog._iter_catalog_files", lambda *_args, **_kwargs: [tmp_path / "data" / "index_price_update" / "BTCUSDT-PERP.BINANCE" / "part.parquet"])

        records = [IndexPriceUpdate(InstrumentId.from_str("BTCUSDT-PERP.BINANCE"), Price.from_str("2.0"), 2, 2)]
        paths = p._write_objects(records, "BTCUSDT-PERP", "indexPriceKlines", "1m", merge=False)

        assert paths
        assert len(captured) == 1
        assert isinstance(captured[0][0][0], IndexPriceUpdate)

    def test_write_objects_uses_nt_direct_updates_for_funding_rate(self, tmp_path: Path, monkeypatch):
        from decimal import Decimal
        from nautilus_trader.model.data import FundingRateUpdate
        from nautilus_trader.model.identifiers import InstrumentId

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        captured = []
        iter_calls = []

        class FakeCatalog:
            def __init__(self, *_args, **_kwargs):
                pass

            def write_data(self, data, *args, **kwargs):
                captured.append((list(data), kwargs))

        def fake_iter(storage, path, recursive=True):
            iter_calls.append((Path(path), recursive))
            return [tmp_path / "data" / "funding_rate_update" / "BTCUSDT-PERP.BINANCE" / "part.parquet"]

        monkeypatch.setattr("tinohelm.data.catalog._catalog_for_root", lambda *_args, **_kwargs: FakeCatalog())
        monkeypatch.setattr("tinohelm.data.catalog.ensure_catalog_dirs", lambda path: path)
        monkeypatch.setattr("tinohelm.data.catalog._iter_catalog_files", fake_iter)

        records = [FundingRateUpdate(InstrumentId.from_str("BTCUSDT-PERP.BINANCE"), Decimal("0.1"), 3, 3, interval=480)]
        paths = p._write_objects(records, "BTCUSDT-PERP", "fundingRate", None, merge=False)

        assert paths
        assert len(captured) == 1
        assert isinstance(captured[0][0][0], FundingRateUpdate)
        assert iter_calls == [
            (tmp_path / "data" / "funding_rate_update" / "BTCUSDT-PERP.BINANCE", True),
            (tmp_path / "data" / "funding_rate_update" / "BTCUSDT-PERP.BINANCE", True),
        ]


class TestBoundedCsvConversion:
    def test_full_file_conversion_reads_csv_payload_without_filesystem_staging(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-trades-2025-01-15.csv", b"a,b\n1,2\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._write_objects = MagicMock(return_value=["memory://catalog/file.parquet"])

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                assert df["a"].tolist() == [1]
                assert kwargs == {"symbol": "BTCUSDT-PERP"}
                return ["object-1"]

        count, paths = p._convert_one_file(
            payload,
            Converter(),
            object(),
            {"symbol": "BTCUSDT-PERP"},
            "BTCUSDT-PERP",
            "trades",
            None,
            False,
        )

        assert count == 1
        assert paths == ["memory://catalog/file.parquet"]
        p._write_objects.assert_called_once_with(
            ["object-1"], "BTCUSDT-PERP", "trades", None, merge=False,
        )

    def test_chunked_conversion_reads_csv_payload_in_chunks(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-trades-2025-01-15.csv", b"1,2\n3,4\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._chunk_rows = 1
        p._tick_chunk_rows = 1
        p._write_objects = MagicMock(side_effect=[
            ["memory://catalog/a.parquet"],
            ["memory://catalog/b.parquet"],
        ])

        class Converter:
            supports_chunked = True

            def validate_schema(self, df):
                assert list(df.columns) == [0, 1]

            def convert_chunk(self, chunk, instrument, **kwargs):
                return [tuple(chunk.iloc[0].tolist())]

        seen_progress: list[int] = []
        count, paths = p._convert_one_file(
            payload,
            Converter(),
            object(),
            {"symbol": "BTCUSDT-PERP"},
            "BTCUSDT-PERP",
            "trades",
            None,
            False,
            chunk_cb=seen_progress.append,
        )

        assert count == 2
        assert paths == ["memory://catalog/a.parquet", "memory://catalog/b.parquet"]
        assert seen_progress == [1, 2]

    def test_cleanup_closes_in_memory_csv_payload(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-trades-2025-01-15.csv", b"1,2\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        p._cleanup_raw_file(payload)

        assert payload.closed


class TestIngestEarlyFailClosed:
    def test_empty_csv_payload_fails_before_catalog_db_update(self, tmp_path: Path, monkeypatch):
        task = SimpleNamespace(url="memory://empty.csv")
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-trades-empty.csv", b""))

        class Converter:
            supports_chunked = True

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
        update_catalog = AsyncMock()
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda data_type: Converter())
        monkeypatch.setattr(p, "_update_db_catalog", update_catalog)

        with pytest.raises(RuntimeError, match="conversion failed"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="trades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        update_catalog.assert_not_awaited()

class TestCatalogStorageStats:
    def test_trade_tick_stats_sum_all_parquet_files_in_source_path(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.strategy.loader_helpers import normalize_symbol

        symbol = "BTCUSDT-PERP"
        catalog_root = resolve_catalog_path(tmp_path, "trades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "price": [100.0, 101.0], "size": [1.0, 2.0]}).write_parquet(trade_dir / "a.parquet")
        pl.DataFrame({"ts_event": [3, 4, 5], "price": [102.0, 103.0, 104.0], "size": [3.0, 4.0, 5.0]}).write_parquet(trade_dir / "b.parquet")
        expected_size = sum(path.stat().st_size for path in trade_dir.glob("*.parquet"))

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "trades", None, "trades") == (5, expected_size)

    def test_trade_tick_stats_unknown_when_any_parquet_count_unreadable(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.strategy.loader_helpers import normalize_symbol

        symbol = "BTCUSDT-PERP"
        catalog_root = resolve_catalog_path(tmp_path, "trades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "price": [100.0, 101.0], "size": [1.0, 2.0]}).write_parquet(trade_dir / "ok.parquet")
        (trade_dir / "bad.parquet").write_text("not parquet", encoding="utf-8")
        expected_size = sum(path.stat().st_size for path in trade_dir.glob("*.parquet"))

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "trades", None, "trades") == (None, expected_size)



class TestUpdateDbCatalog:
    def test_updates_incremental_stats_without_full_storage_scan(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None
                self.commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(side_effect=AssertionError("full scan called"))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="trades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=15, size_bytes=150, source_type="trades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 3)
        assert fake_session.row.record_count == 25
        assert fake_session.row.size_bytes == 250
        assert fake_session.commits == 2
        p._catalog_storage_stats.assert_not_called()

    def test_disjoint_update_with_unknown_new_record_count_clears_count_without_full_scan(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None
                self.commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(side_effect=AssertionError("full scan called"))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="trades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=None, size_bytes=150, source_type="trades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 3)
        assert fake_session.row.record_count is None
        assert fake_session.row.size_bytes == 250
        assert fake_session.commits == 2
        p._catalog_storage_stats.assert_not_called()

    def test_overlapping_updates_replace_stats_from_storage_scan(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None
                self.commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(12, 120))
        p._catalog_storage_coverage = MagicMock(return_value=(date(2025, 1, 3), date(2025, 1, 4)))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=10, size_bytes=100, source_type="trades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="trades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="trades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 3)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 12
        assert fake_session.row.size_bytes == 120
        assert fake_session.commits == 3
        assert p._catalog_storage_stats.call_count == 2

    def test_overlapping_update_preserves_dates_when_storage_coverage_unknown(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(2, 200))
        p._catalog_storage_coverage = MagicMock(return_value=None)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 1), date(2025, 1, 31),
                record_count=31, size_bytes=3100, source_type="trades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 15), date(2025, 1, 15),
                record_count=2, size_bytes=200, source_type="trades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 1)
        assert fake_session.row.end_date == date(2025, 1, 31)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == 200

    def test_overlapping_update_recomputes_dates_from_storage_coverage(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.db.models import DataCatalog
        from tinohelm.strategy.loader_helpers import normalize_symbol

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        replacement_day = date(2025, 1, 15)
        day_start_ns = 1_736_899_200_000_000_000
        catalog_root = resolve_catalog_path(tmp_path, "trades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({
            "ts_event": [day_start_ns, day_start_ns + 60_000_000_000],
            "price": [100.0, 101.0],
            "size": [1.0, 2.0],
        }).write_parquet(trade_dir / "replacement-day.parquet")

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "trades", None,
                date(2025, 1, 1), date(2025, 1, 31),
                record_count=31, size_bytes=3100, source_type="trades",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "trades", None,
                replacement_day, replacement_day,
                record_count=2, size_bytes=trade_dir.joinpath("replacement-day.parquet").stat().st_size,
                source_type="trades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == replacement_day
        assert fake_session.row.end_date == replacement_day
        assert fake_session.row.record_count == 2

    def test_mark_price_disjoint_update_refreshes_direct_update_stats(self, tmp_path: Path):
        from nautilus_trader.model.data import MarkPriceUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from nautilus_trader.model.objects import Price
        from tinohelm.data.catalog import _catalog_for_root, mark_price_update_dir
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        inst = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        catalog = _catalog_for_root(tmp_path)
        for ts, price in ((1, "1.0"), (2, "2.0"), (3, "3.0"), (4, "4.0")):
            catalog.write_data([MarkPriceUpdate(inst, Price.from_str(price), ts, ts)], skip_disjoint_check=True)
        update_dir = mark_price_update_dir(symbol, tmp_path)
        size_bytes = sum(path.stat().st_size for path in update_dir.glob("*.parquet"))

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "markPriceKlines", "1m",
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=size_bytes, source_type="markPriceKlines",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "markPriceKlines", "1m",
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=size_bytes, source_type="markPriceKlines",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 4
        assert fake_session.row.size_bytes == size_bytes

    def test_index_price_disjoint_update_refreshes_direct_update_stats(self, tmp_path: Path):
        from nautilus_trader.model.data import IndexPriceUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from nautilus_trader.model.objects import Price
        from tinohelm.data.catalog import _catalog_for_root, index_price_update_dir
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        inst = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        catalog = _catalog_for_root(tmp_path)
        for ts, price in ((1, "1.0"), (2, "2.0"), (3, "3.0"), (4, "4.0")):
            catalog.write_data([IndexPriceUpdate(inst, Price.from_str(price), ts, ts)], skip_disjoint_check=True)
        update_dir = index_price_update_dir(symbol, tmp_path)
        size_bytes = sum(path.stat().st_size for path in update_dir.glob("*.parquet"))

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "indexPriceKlines", "1m",
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=size_bytes, source_type="indexPriceKlines",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "indexPriceKlines", "1m",
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=size_bytes, source_type="indexPriceKlines",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 4
        assert fake_session.row.size_bytes == size_bytes

    def test_funding_rate_disjoint_update_refreshes_single_file_stats(self, tmp_path: Path):
        from decimal import Decimal
        from nautilus_trader.model.data import FundingRateUpdate
        from nautilus_trader.model.identifiers import InstrumentId
        from tinohelm.data.catalog import _catalog_for_root, funding_rate_update_dir
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        symbol = "BTCUSDT-PERP"
        inst = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        catalog = _catalog_for_root(tmp_path)
        for ts in (1, 2, 3, 4):
            catalog.write_data([FundingRateUpdate(inst, Decimal("0.1"), ts, ts, interval=480)], skip_disjoint_check=True)
        update_dir = funding_rate_update_dir(symbol, tmp_path)
        size_bytes = sum(path.stat().st_size for path in update_dir.glob("*.parquet"))

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "fundingRate", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=size_bytes, source_type="fundingRate",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "fundingRate", None,
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=size_bytes, source_type="fundingRate",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 4
        assert fake_session.row.size_bytes == size_bytes

    def test_bar_disjoint_update_refreshes_size_from_storage_without_recounting(self, tmp_path: Path):
        from tinohelm.db.models import DataCatalog

        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(5, 250))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "klines", "1m",
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="klines",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "klines", "1m",
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=2, size_bytes=999, source_type="klines",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 12
        assert fake_session.row.size_bytes == 250
        p._catalog_storage_stats.assert_called_once_with("BTCUSDT-PERP", "klines", "1m", "klines")

    def test_overlapping_update_keeps_unknown_storage_record_count(self, tmp_path: Path):
        class FakeSession:
            def __init__(self):
                self.row = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def execute(self, stmt):
                return SimpleNamespace(scalar_one_or_none=lambda: self.row)

            def add(self, row):
                self.row = row

            async def commit(self):
                pass

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._catalog_storage_stats = MagicMock(return_value=(None, 220))

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=10, size_bytes=100, source_type="trades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "trades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="trades",
            ))

        assert fake_session.row.record_count is None
        assert fake_session.row.size_bytes == 220


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

    def test_ingest_no_interval_required_for_trades(self):
        """trades does not need interval — should not raise ValueError on that check."""
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
                data_type="trades",
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


class TestIngestFailClosed:
    def test_conversion_write_failure_raises_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-trades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-trades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._write_objects = MagicMock(side_effect=RuntimeError("remote parquet write failed"))
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="remote parquet write failed"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="trades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        p._update_db_catalog.assert_not_awaited()

    def test_csv_read_failure_raises_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-trades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-trades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(p, "_detect_header", MagicMock(side_effect=OSError("csv header unreadable")))
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="csv header unreadable"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="trades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        p._update_db_catalog.assert_not_awaited()

    def test_nonempty_conversion_with_no_written_files_raises_before_catalog_db_update(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        task = SimpleNamespace(url="https://data.binance.vision/BTCUSDT-trades.zip")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-trades-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                pass

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._write_objects = MagicMock(return_value=[])
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="wrote no parquet files"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="trades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 1),
            ))

        p._update_db_catalog.assert_not_awaited()

    def test_middle_vision_download_error_still_fails_closed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        tasks = [
            SimpleNamespace(
                url=f"https://data.binance.vision/BTCUSDT-trades-2025-01-0{day}.zip",
                granularity="daily",
                dest_path=Path(f"BTCUSDT-trades-2025-01-0{day}.csv"),
            )
            for day in (1, 2, 3)
        ]

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return tasks

            async def execute_task(self, task):
                if task is tasks[1]:
                    raise FileNotFoundError("vision middle 404")
                return _csv_payload(task.dest_path.name, b"a,b\n1,2\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        p._write_objects = MagicMock(return_value=["/catalog/ticks/trades/day.parquet"])
        p._update_db_catalog = AsyncMock()

        with pytest.raises(RuntimeError, match="download failed"):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="trades",
                start=date(2025, 1, 1),
                end=date(2025, 1, 3),
            ))

        p._update_db_catalog.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4b. ingest() — funding-rate cache short-circuit
# ---------------------------------------------------------------------------

class TestFundingRateCacheShortCircuit:
    """The pipeline must skip plan_downloads when the JSON cache already
    covers [start, end] — defense-in-depth against repeated Binance hits.
    """

    def test_ingest_skips_when_funding_cache_covers_range(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.02},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.funding_parquet_covers",
            lambda self, symbol, start, end: True,
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.side_effect = AssertionError(
            "plan_downloads must not run when cache covers range"
        )
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2024, 1, 5),
                end=date(2024, 1, 20),
            ))

        assert result.skipped is True
        assert result.objects_count == 0
        mock_dl.plan_downloads.assert_not_called()

    def test_ingest_does_not_skip_when_json_cache_covers_but_primary_parquet_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc

        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.02},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.funding_parquet_covers",
            lambda self, symbol, start, end: False,
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        result = asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="fundingRate",
            start=date(2024, 1, 5),
            end=date(2024, 1, 20),
        ))

        assert result.skipped is True
        mock_dl.plan_downloads.assert_called_once()

    def test_ingest_runs_when_funding_cache_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []  # also no tasks → skipped
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2024, 1, 1),
                end=date(2024, 1, 5),
            ))

        # Empty cache means we fall through to plan_downloads as before.
        mock_dl.plan_downloads.assert_called_once()


class TestTradeTickCoverageShortCircuit:
    def test_ingest_skips_when_trade_tick_catalog_covers_range(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.side_effect = AssertionError(
            "plan_downloads must not run when trade_tick catalog covers range"
        )
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="trades",
                start=date(2024, 1, 5),
                end=date(2024, 1, 20),
            ))

        assert result.skipped is True
        assert result.objects_count == 0
        mock_dl.plan_downloads.assert_not_called()

    def test_ingest_trade_tick_partial_coverage_plans_only_missing_slice(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        planned_ranges: list[tuple[date, date]] = []
        consolidate_ranges: list[tuple[date | None, date | None]] = []

        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [
                (date(2024, 1, 11), date(2024, 1, 11)),
            ],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **kwargs):
                planned_ranges.append((kwargs["start"], kwargs["end"]))
                return [
                    SimpleNamespace(
                        url="https://example.com/day.zip",
                        checksum_url="https://example.com/day.zip.CHECKSUM",
                        dest_path=Path("BTCUSDT-trades-2024-01-11.csv"),
                        zip_path=Path("BTCUSDT-trades-2024-01-11.zip"),
                        granularity="daily",
                    )
                ]

            async def execute_task(self, task):
                return _csv_payload(task.dest_path.name, b"1,65000,0.1,6500,1704931200000,true\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                return None

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        monkeypatch.setattr(p, "_write_objects", lambda *args, **kwargs: ["/catalog/ticks/trades/day.parquet"])
        monkeypatch.setattr(p, "_cleanup_raw_file", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(p, "_detect_gaps_for_backfill", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(p, "_catalog_storage_stats", lambda *_args, **_kwargs: (1, 1))
        async def _noop_update(*a, **kw):
            return None
        monkeypatch.setattr(p, "_update_db_catalog", _noop_update)
        monkeypatch.setattr(
            p,
            "_consolidate_catalog_data",
            lambda symbol, data_type, interval, start, end: consolidate_ranges.append((start, end)),
        )

        result = asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="trades",
            start=date(2024, 1, 1),
            end=date(2024, 1, 20),
        ))

        assert result.skipped is False
        assert planned_ranges == [(date(2024, 1, 11), date(2024, 1, 11))]
        assert consolidate_ranges == [(date(2024, 1, 11), date(2024, 1, 11))]

    def test_ingest_trade_tick_multiple_missing_slices_plan_each_slice(self, monkeypatch: pytest.MonkeyPatch):
        planned_ranges: list[tuple[date, date]] = []
        consolidate_ranges: list[tuple[date | None, date | None]] = []

        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [
                (date(2024, 1, 11), date(2024, 1, 11)),
                (date(2024, 1, 15), date(2024, 1, 15)),
            ],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **kwargs):
                planned_ranges.append((kwargs["start"], kwargs["end"]))
                day = kwargs["start"].isoformat()
                return [
                    SimpleNamespace(
                        url=f"https://example.com/{day}.zip",
                        checksum_url=f"https://example.com/{day}.zip.CHECKSUM",
                        dest_path=Path(f"BTCUSDT-trades-{day}.csv"),
                        zip_path=Path(f"BTCUSDT-trades-{day}.zip"),
                        granularity="daily",
                    )
                ]

            async def execute_task(self, task):
                return _csv_payload(task.dest_path.name, b"1,65000,0.1,6500,1704931200000,true\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                return None

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        monkeypatch.setattr(p, "_write_objects", lambda *args, **kwargs: ["/catalog/ticks/trades/day.parquet"])
        monkeypatch.setattr(p, "_cleanup_raw_file", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(p, "_detect_gaps_for_backfill", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(p, "_catalog_storage_stats", lambda *_args, **_kwargs: (1, 1))
        async def _noop_update(*a, **kw):
            return None
        monkeypatch.setattr(p, "_update_db_catalog", _noop_update)
        monkeypatch.setattr(
            p,
            "_consolidate_catalog_data",
            lambda symbol, data_type, interval, start, end: consolidate_ranges.append((start, end)),
        )

        result = asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="trades",
            start=date(2024, 1, 1),
            end=date(2024, 1, 20),
        ))

        assert result.skipped is False
        assert planned_ranges == [
            (date(2024, 1, 11), date(2024, 1, 11)),
            (date(2024, 1, 15), date(2024, 1, 15)),
        ]
        assert consolidate_ranges == [(date(2024, 1, 11), date(2024, 1, 15))]

    def test_ingest_trade_tick_does_not_backfill_gaps_outside_requested_missing_slices(self, monkeypatch: pytest.MonkeyPatch):
        planned_ranges: list[tuple[date, date]] = []

        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [
                (date(2024, 1, 11), date(2024, 1, 11)),
            ],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **kwargs):
                planned_ranges.append((kwargs["start"], kwargs["end"]))
                day = kwargs["start"].isoformat()
                return [
                    SimpleNamespace(
                        url=f"https://example.com/{day}.zip",
                        checksum_url=f"https://example.com/{day}.zip.CHECKSUM",
                        dest_path=Path(f"BTCUSDT-trades-{day}.csv"),
                        zip_path=Path(f"BTCUSDT-trades-{day}.zip"),
                        granularity="daily",
                    )
                ]

            async def execute_task(self, task):
                return _csv_payload(task.dest_path.name, b"1,65000,0.1,6500,1704931200000,true\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                return None

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        monkeypatch.setattr(p, "_write_objects", lambda *args, **kwargs: ["/catalog/ticks/trades/day.parquet"])
        monkeypatch.setattr(p, "_cleanup_raw_file", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            p,
            "_detect_gaps_for_backfill",
            lambda *_args, **_kwargs: [(date(2024, 1, 30), date(2024, 1, 30))],
        )
        monkeypatch.setattr(p, "_catalog_storage_stats", lambda *_args, **_kwargs: (1, 1))
        async def _noop_update(*a, **kw):
            return None
        monkeypatch.setattr(p, "_update_db_catalog", _noop_update)
        monkeypatch.setattr(p, "_consolidate_catalog_data", lambda *args, **kwargs: None)

        asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="trades",
            start=date(2024, 1, 1),
            end=date(2024, 1, 20),
        ))

        assert planned_ranges == [(date(2024, 1, 11), date(2024, 1, 11))]

    def test_ingest_trade_tick_backfill_clips_detected_gap_to_requested_missing_slice(self, monkeypatch: pytest.MonkeyPatch):
        planned_ranges: list[tuple[date, date]] = []

        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [
                (date(2024, 1, 11), date(2024, 1, 11)),
            ],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **kwargs):
                planned_ranges.append((kwargs["start"], kwargs["end"]))
                day = kwargs["start"].isoformat()
                return [
                    SimpleNamespace(
                        url=f"https://example.com/{day}.zip",
                        checksum_url=f"https://example.com/{day}.zip.CHECKSUM",
                        dest_path=Path(f"BTCUSDT-trades-{day}.csv"),
                        zip_path=Path(f"BTCUSDT-trades-{day}.zip"),
                        granularity="daily",
                    )
                ]

            async def execute_task(self, task):
                return _csv_payload(task.dest_path.name, b"1,65000,0.1,6500,1704931200000,true\n")

        class Converter:
            supports_chunked = False

            def validate_schema(self, df):
                return None

            def convert(self, df, instrument, **kwargs):
                return [SimpleNamespace(ts_init=1)]

        p.downloader = Downloader()
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        monkeypatch.setattr(p, "_write_objects", lambda *args, **kwargs: ["/catalog/ticks/trades/day.parquet"])
        monkeypatch.setattr(p, "_cleanup_raw_file", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            p,
            "_detect_gaps_for_backfill",
            lambda *_args, **_kwargs: [(date(2024, 1, 10), date(2024, 1, 12))],
        )
        monkeypatch.setattr(p, "_catalog_storage_stats", lambda *_args, **_kwargs: (1, 1))
        async def _noop_update(*a, **kw):
            return None
        monkeypatch.setattr(p, "_update_db_catalog", _noop_update)
        monkeypatch.setattr(p, "_consolidate_catalog_data", lambda *args, **kwargs: None)

        asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="trades",
            start=date(2024, 1, 1),
            end=date(2024, 1, 20),
        ))

        assert planned_ranges == [
            (date(2024, 1, 11), date(2024, 1, 11)),
            (date(2024, 1, 11), date(2024, 1, 11)),
        ]

    def test_ingest_runs_when_funding_cache_has_gap(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        import tinohelm.data.funding_cache as fc
        # Cache covers up to 2024-01-10; requested end is 2024-01-20 → gap.
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},  # 2024-01-01
            {"funding_time_ms": 1_704_844_800_000, "funding_rate": 0.02},  # 2024-01-10
        ])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="fundingRate",
                start=date(2024, 1, 1),
                end=date(2024, 1, 20),
            ))

        # Gap detected → plan_downloads must run.
        mock_dl.plan_downloads.assert_called_once()

    def test_short_circuit_only_applies_to_funding_rate(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        """A full funding cache must not short-circuit a klines ingest."""
        import tinohelm.data.funding_cache as fc
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="klines",
                interval="1m",
                start=date(2024, 1, 5),
                end=date(2024, 1, 20),
            ))

        mock_dl.plan_downloads.assert_called_once()


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
# _parquet_time_range — stale dircache resilience
# ---------------------------------------------------------------------------


class TestParquetTimeRangeStaleCache:
    """After consolidation rewrites files via a separate s3fs instance,
    the TinoHelm storage instance may hold stale ETag/path references.
    _parquet_time_range must tolerate these gracefully."""

    def test_returns_none_on_file_not_found(self):
        """FileNotFoundError from stale dircache → None (skip file)."""
        storage = MagicMock()
        storage.provider = "s3"
        storage.open_input_file.side_effect = FileNotFoundError(
            "trade-data/catalog/data/bar/BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL/"
            "2023-05-17T00-00-59-999000000Z_2026-05-16T23-59-59-999000000Z.parquet"
        )
        obj = MagicMock()

        result = BinanceVisionPipeline._parquet_time_range(obj, storage=storage)

        assert result is None

    def test_returns_none_on_etag_mismatch_oserror(self):
        """OSError errno 16 (stale ETag) → None (skip file)."""
        storage = MagicMock()
        storage.provider = "s3"
        storage.open_input_file.side_effect = OSError(
            16,
            'The remote file corresponding to filename '
            'trade-data/catalog/...parquet and Etag "abc123" no longer exists.',
        )
        obj = MagicMock()

        result = BinanceVisionPipeline._parquet_time_range(obj, storage=storage)

        assert result is None

    def test_propagates_permission_error(self):
        """PermissionError (auth failure) must NOT be swallowed."""
        storage = MagicMock()
        storage.provider = "s3"
        storage.open_input_file.side_effect = PermissionError("Access Denied")
        obj = MagicMock()

        with pytest.raises(PermissionError):
            BinanceVisionPipeline._parquet_time_range(obj, storage=storage)

    def test_propagates_generic_oserror(self):
        """OSError with non-16 errno must NOT be swallowed."""
        storage = MagicMock()
        storage.provider = "s3"
        storage.open_input_file.side_effect = OSError(28, "No space left on device")
        obj = MagicMock()

        with pytest.raises(OSError):
            BinanceVisionPipeline._parquet_time_range(obj, storage=storage)


class TestCatalogStorageCoverageInvalidatesCache:
    """_catalog_storage_coverage must invalidate dircache before listing
    so it doesn't see files deleted by consolidation's separate fs instance."""

    def test_invalidates_dircache_before_iter_files(self):
        """Storage fs.invalidate_cache is called before iter_files."""
        p = BinanceVisionPipeline(catalog_path="/tmp/test")

        mock_storage = MagicMock()
        mock_storage.provider = "s3"
        mock_storage.iter_files.return_value = []
        mock_fs = MagicMock()
        mock_storage.fs = mock_fs
        p._storage = mock_storage

        p._catalog_item_dir = MagicMock(return_value=Path("trade-data/catalog/data/bar/X"))

        result = p._catalog_storage_coverage("BTCUSDT-PERP", "klines", "1m", "klines")

        mock_fs.invalidate_cache.assert_called_once_with()
        assert result is None  # no files → None
