"""Tests for tinohelm.data.pipeline.BinanceVisionPipeline.

All network, DB, and filesystem side-effects are mocked.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from tinohelm.data.downloader import VisionCsvPayload
from tinohelm.data.pipeline import BinanceVisionPipeline, IngestResult, _ns_to_utc_date


def _csv_payload(name: str, content: bytes) -> VisionCsvPayload:
    file_obj = BytesIO(content)
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


class TestBoundedCsvConversion:
    def test_full_file_conversion_reads_csv_payload_without_filesystem_staging(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-aggTrades-2025-01-15.csv", b"a,b\n1,2\n")
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
            "metrics",
            None,
            False,
        )

        assert count == 1
        assert paths == ["memory://catalog/file.parquet"]
        p._write_objects.assert_called_once_with(
            ["object-1"], "BTCUSDT-PERP", "metrics", None, merge=False,
        )

    def test_chunked_conversion_reads_csv_payload_in_chunks(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-aggTrades-2025-01-15.csv", b"1,2\n3,4\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._chunk_rows = 1
        p._agg_trades_chunk_rows = 1
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
            "aggTrades",
            None,
            False,
            chunk_cb=seen_progress.append,
        )

        assert count == 2
        assert paths == ["memory://catalog/a.parquet", "memory://catalog/b.parquet"]
        assert seen_progress == [1, 2]

    def test_cleanup_closes_in_memory_csv_payload(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-aggTrades-2025-01-15.csv", b"1,2\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        p._cleanup_raw_file(payload)

        assert payload.closed


class TestCatalogStorageStats:
    def test_remote_single_file_metrics_write_passes_storage_for_in_memory_upload(self, tmp_path: Path, monkeypatch):
        from tinohelm.data.catalog import metrics_parquet_path

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)
        uploaded: list[tuple[Path, bytes]] = []

        class RemoteStorage:
            provider = "s3"
            catalog_root = tmp_path

            def upload_bytes(self, logical_path, payload):
                uploaded.append((Path(logical_path), payload))
                return "s3://bucket/catalog/metrics.parquet"

            def upload_path(self, local_path, *, logical_path=None):  # pragma: no cover - must not be called
                raise AssertionError("remote metrics writes must not upload a local staged file")

        def fake_write_metrics(records, write_symbol, catalog_root, storage=None):
            assert records == [object_marker]
            assert write_symbol == symbol
            assert Path(catalog_root) == tmp_path
            assert storage is p._storage
            assert not path.exists()
            storage.upload_bytes(path, b"merged-in-memory")
            return path

        object_marker = object()
        monkeypatch.setattr("tinohelm.data.catalog.write_metrics_parquet", fake_write_metrics)
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._storage = RemoteStorage()
        p.catalog_path = str(tmp_path)

        result = p._write_objects([object_marker], symbol, "metrics", None)

        assert result == [str(path)]
        assert uploaded == [(path, b"merged-in-memory")]
        assert not path.exists()

    def test_written_file_size_uses_remote_storage_iter_files(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)

        class RemoteStorage:
            provider = "s3"

            def iter_files(self, prefix, *, suffix="", recursive=True):
                assert Path(prefix) == path
                assert suffix == ".parquet"
                assert recursive is False
                return iter([SimpleNamespace(path=path, size=123)])

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._storage = RemoteStorage()

        assert p._written_file_size({str(path)}) == 123

    def test_trade_tick_stats_sum_all_parquet_files_in_source_path(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.strategy.loader_helpers import normalize_symbol

        symbol = "BTCUSDT-PERP"
        catalog_root = resolve_catalog_path(tmp_path, "aggTrades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "price": [100.0, 101.0], "size": [1.0, 2.0]}).write_parquet(trade_dir / "a.parquet")
        pl.DataFrame({"ts_event": [3, 4, 5], "price": [102.0, 103.0, 104.0], "size": [3.0, 4.0, 5.0]}).write_parquet(trade_dir / "b.parquet")
        expected_size = sum(path.stat().st_size for path in trade_dir.glob("*.parquet"))

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "aggTrades", None, "aggTrades") == (5, expected_size)

    def test_trade_tick_stats_unknown_when_any_parquet_count_unreadable(self, tmp_path: Path):
        from tinohelm.data.catalog import resolve_catalog_path
        from tinohelm.strategy.loader_helpers import normalize_symbol

        symbol = "BTCUSDT-PERP"
        catalog_root = resolve_catalog_path(tmp_path, "aggTrades")
        trade_dir = catalog_root / "data" / "trade_tick" / normalize_symbol(symbol)
        trade_dir.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "price": [100.0, 101.0], "size": [1.0, 2.0]}).write_parquet(trade_dir / "ok.parquet")
        (trade_dir / "bad.parquet").write_text("not parquet", encoding="utf-8")
        expected_size = sum(path.stat().st_size for path in trade_dir.glob("*.parquet"))

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "aggTrades", None, "aggTrades") == (None, expected_size)

    def test_metrics_stats_count_rows_from_single_file_path(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path

        symbol = "BTCUSDT-PERP"
        path = metrics_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3], "open_interest": [10.0, 11.0, 12.0]}).write_parquet(path)

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "metrics", None, "metrics") == (3, path.stat().st_size)

    def test_book_depth_stats_count_rows_from_single_file_path(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path

        symbol = "BTCUSDT-PERP"
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        p = BinanceVisionPipeline(catalog_path=tmp_path)

        assert p._catalog_storage_stats(symbol, "bookDepth", None, "bookDepth") == (2, path.stat().st_size)
        assert p._catalog_storage_stats(symbol, "order_book_delta", None, "order_book_delta") == (2, path.stat().st_size)


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
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=15, size_bytes=150, source_type="aggTrades",
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
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 3),
                record_count=None, size_bytes=150, source_type="aggTrades",
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
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="aggTrades",
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
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 1), date(2025, 1, 31),
                record_count=31, size_bytes=3100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 15), date(2025, 1, 15),
                record_count=2, size_bytes=200, source_type="aggTrades",
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
        catalog_root = resolve_catalog_path(tmp_path, "aggTrades")
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
                symbol, "aggTrades", None,
                date(2025, 1, 1), date(2025, 1, 31),
                record_count=31, size_bytes=3100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "aggTrades", None,
                replacement_day, replacement_day,
                record_count=2, size_bytes=trade_dir.joinpath("replacement-day.parquet").stat().st_size,
                source_type="aggTrades",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == replacement_day
        assert fake_session.row.end_date == replacement_day
        assert fake_session.row.record_count == 2

    def test_metrics_overlapping_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path
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
        path = metrics_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3], "open_interest": [10.0, 11.0, 12.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=1, size_bytes=1, source_type="metrics",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=1, size_bytes=1, source_type="metrics",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 3
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_book_depth_overlapping_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path
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
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=1, size_bytes=1, source_type="bookDepth",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=1, size_bytes=1, source_type="bookDepth",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_order_book_delta_overlapping_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path
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
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "order_book_delta", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=1, size_bytes=1, source_type="order_book_delta",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "order_book_delta", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=1, size_bytes=1, source_type="order_book_delta",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.data_type == "order_book_delta"
        assert fake_session.row.start_date == date(2025, 1, 2)
        assert fake_session.row.end_date == date(2025, 1, 4)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_metrics_disjoint_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import metrics_parquet_path
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
        path = metrics_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3], "open_interest": [10.0, 11.0, 12.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=path.stat().st_size, source_type="metrics",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "metrics", None,
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=path.stat().st_size, source_type="metrics",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 3
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_book_depth_disjoint_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import book_depth_parquet_path
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
        path = book_depth_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2], "percentage": [0.05, 0.10], "depth": [100.0, 200.0]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=path.stat().st_size, source_type="bookDepth",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "bookDepth", None,
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=path.stat().st_size, source_type="bookDepth",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 2
        assert fake_session.row.size_bytes == path.stat().st_size

    def test_funding_rate_disjoint_update_refreshes_single_file_stats(self, tmp_path: Path):
        from tinohelm.data.catalog import funding_rate_parquet_path
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
        path = funding_rate_parquet_path(symbol, tmp_path)
        path.parent.mkdir(parents=True)
        pl.DataFrame({"ts_event": [1, 2, 3, 4], "funding_rate": [0.1, 0.2, 0.3, 0.4]}).write_parquet(path)

        fake_session = FakeSession()
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        with patch("tinohelm.db.session.get_session_factory", return_value=lambda: fake_session):
            asyncio.run(p._update_db_catalog(
                symbol, "fundingRate", None,
                date(2025, 1, 2), date(2025, 1, 2),
                record_count=1, size_bytes=path.stat().st_size, source_type="fundingRate",
            ))
            asyncio.run(p._update_db_catalog(
                symbol, "fundingRate", None,
                date(2025, 1, 4), date(2025, 1, 4),
                record_count=1, size_bytes=path.stat().st_size, source_type="fundingRate",
            ))

        assert isinstance(fake_session.row, DataCatalog)
        assert fake_session.row.record_count == 4
        assert fake_session.row.size_bytes == path.stat().st_size

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
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 2), date(2025, 1, 3),
                record_count=10, size_bytes=100, source_type="aggTrades",
            ))
            asyncio.run(p._update_db_catalog(
                "BTCUSDT-PERP", "aggTrades", None,
                date(2025, 1, 3), date(2025, 1, 4),
                record_count=10, size_bytes=100, source_type="aggTrades",
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
        # Seed a cache spanning 2024-01-01 → 2024-02-01 (UTC ms).
        # 1704067200000 = 2024-01-01 00:00:00 UTC
        # 1706745600000 = 2024-02-01 00:00:00 UTC
        monkeypatch.setattr(fc, "_load_cache", lambda sym: [
            {"funding_time_ms": 1_704_067_200_000, "funding_rate": 0.01},
            {"funding_time_ms": 1_705_276_800_000, "funding_rate": 0.02},
            {"funding_time_ms": 1_706_745_600_000, "funding_rate": 0.03},
        ])

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        # plan_downloads must NOT be called because we short-circuit first.
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
