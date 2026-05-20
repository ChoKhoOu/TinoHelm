"""Tests for tinohelm.data.pipeline.BinanceVisionPipeline.

All network, DB, and filesystem side-effects are mocked.
"""
from __future__ import annotations

import asyncio
import threading
from io import BytesIO
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from tinohelm.data.downloader import VisionCsvPayload, VisionZipCsvPayload
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

    def test_conversion_reads_full_csv_payload_once(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-markPriceKlines-2025-01-15.csv", b"1,2\n3,4\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._write_objects = MagicMock(return_value=["memory://catalog/full.parquet"])

        class Converter:
            def validate_schema(self, df):
                assert list(df.columns) == [0, 1]

            def convert(self, df, instrument, **kwargs):
                assert len(df) == 2
                return [tuple(row) for row in df.itertuples(index=False, name=None)]

        count, paths = p._convert_one_file(
            payload,
            Converter(),
            object(),
            {"symbol": "BTCUSDT-PERP"},
            "BTCUSDT-PERP",
            "markPriceKlines",
            None,
            False,
        )

        assert count == 2
        assert paths == ["memory://catalog/full.parquet"]
        p._write_objects.assert_called_once()

    def test_trades_conversion_reads_full_daily_file_once(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-trades-2025-01-15.csv", b"1,2\n3,4\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._write_objects = MagicMock(return_value=["memory://catalog/day.parquet"])

        class Converter:
            def validate_schema(self, df):
                assert list(df.columns) == [0, 1]

            def convert(self, df, instrument, **kwargs):
                assert len(df) == 2
                return [tuple(row) for row in df.itertuples(index=False, name=None)]

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

        assert count == 2
        assert paths == ["memory://catalog/day.parquet"]
        p._write_objects.assert_called_once()

    def test_bookticker_conversion_reads_full_daily_file_once(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-bookTicker-2025-01-15.csv", b"a,b\n1,2\n3,4\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p._write_objects = MagicMock(return_value=["memory://catalog/day.parquet"])

        class Converter:
            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                assert len(df) == 2
                return [tuple(row) for row in df.itertuples(index=False, name=None)]

        count, paths = p._convert_one_file(
            payload,
            Converter(),
            object(),
            {"symbol": "BTCUSDT-PERP"},
            "BTCUSDT-PERP",
            "bookTicker",
            None,
            False,
        )

        assert count == 2
        assert paths == ["memory://catalog/day.parquet"]
        p._write_objects.assert_called_once()

    def test_cleanup_closes_in_memory_csv_payload(self, tmp_path: Path):
        payload = _csv_payload("BTCUSDT-trades-2025-01-15.csv", b"1,2\n")
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        p._cleanup_raw_file(payload)

        assert payload.closed

    def test_cleanup_removes_zip_backed_csv_payload(self, tmp_path: Path):
        zip_path = tmp_path / "vision-temp.zip"
        zip_path.write_bytes(b"zip")
        payload = VisionZipCsvPayload(
            name="BTCUSDT-trades-2025-01-15.csv",
            zip_path=zip_path,
            member="BTCUSDT-trades-2025-01-15.csv",
        )
        p = BinanceVisionPipeline(catalog_path=tmp_path)

        p._cleanup_raw_file(payload)

        assert payload.closed
        assert not zip_path.exists()

    @pytest.mark.asyncio
    async def test_ingest_cancellation_cleans_zip_backed_payloads(self, tmp_path: Path, monkeypatch):
        first_payload = VisionZipCsvPayload(
            name="BTCUSDT-trades-2025-01-01.csv",
            zip_path=tmp_path / "first.zip",
            member="BTCUSDT-trades-2025-01-01.csv",
        )
        second_payload = VisionZipCsvPayload(
            name="BTCUSDT-trades-2025-01-02.csv",
            zip_path=tmp_path / "second.zip",
            member="BTCUSDT-trades-2025-01-02.csv",
        )
        first_payload.zip_path.write_bytes(b"zip")
        second_payload.zip_path.write_bytes(b"zip")
        tasks = [
            SimpleNamespace(url="memory://one", granularity="daily", dest_path=Path("BTCUSDT-trades-2025-01-01.csv")),
            SimpleNamespace(url="memory://two", granularity="daily", dest_path=Path("BTCUSDT-trades-2025-01-02.csv")),
        ]
        started = threading.Event()
        second_returned = asyncio.Event()
        release_convert = threading.Event()
        payload_iter = iter([first_payload, second_payload])

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return tasks

            async def execute_task(self, task):
                payload = next(payload_iter)
                if task is tasks[1]:
                    second_returned.set()
                return payload

        class Converter:
            pass

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = Downloader()
        p._convert_workers = 1
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        monkeypatch.setattr("tinohelm.data.catalog.CatalogSession.missing_date_slices", lambda *_args, **_kwargs: [(date(2025, 1, 1), date(2025, 1, 2))])

        def fake_convert(*_args, **_kwargs):
            started.set()
            release_convert.wait(timeout=5)
            return (1, ["memory://catalog/file.parquet"])

        monkeypatch.setattr(p, "_convert_one_file", fake_convert)
        monkeypatch.setattr(p, "_consolidate_catalog_data", MagicMock())
        monkeypatch.setattr(p, "_detect_gaps_for_backfill", MagicMock(return_value=[]))
        p._update_db_catalog = AsyncMock()
        monkeypatch.setattr(p, "_catalog_storage_stats", MagicMock(return_value=(1, 1)))

        ingest_task = asyncio.create_task(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="trades",
            start=date(2025, 1, 1),
            end=date(2025, 1, 2),
        ))

        await asyncio.to_thread(started.wait)
        await second_returned.wait()
        ingest_task.cancel()
        release_convert.set()

        with pytest.raises(asyncio.CancelledError):
            await ingest_task

        assert first_payload.closed
        assert second_payload.closed
        assert not first_payload.zip_path.exists()
        assert not second_payload.zip_path.exists()
        p._update_db_catalog.assert_not_awaited()


class TestGlobalConvertWriteGate:
    @pytest.mark.asyncio
    async def test_two_ingests_can_download_in_parallel_but_serialize_convert_write(self, tmp_path: Path, monkeypatch):
        btc_task = SimpleNamespace(
            url="memory://btc",
            granularity="daily",
            dest_path=Path("BTCUSDT-trades-2025-01-01.csv"),
        )
        eth_task = SimpleNamespace(
            url="memory://eth",
            granularity="daily",
            dest_path=Path("ETHUSDT-trades-2025-01-01.csv"),
        )
        btc_downloaded = asyncio.Event()
        eth_downloaded = asyncio.Event()
        first_convert_entered = threading.Event()
        second_convert_entered = threading.Event()
        release_first_convert = threading.Event()
        active_lock = threading.Lock()
        active_converts = 0
        max_active_converts = 0

        class Downloader:
            concurrency = 1

            def __init__(self, task, payload, downloaded_event: asyncio.Event):
                self._task = task
                self._payload = payload
                self._downloaded_event = downloaded_event

            def plan_downloads(self, **_kwargs):
                return [self._task]

            async def execute_task(self, _task):
                self._downloaded_event.set()
                return self._payload

        class Converter:
            def validate_schema(self, df):
                return None

            def convert(self, df, instrument, **kwargs):
                nonlocal active_converts, max_active_converts
                with active_lock:
                    active_converts += 1
                    max_active_converts = max(max_active_converts, active_converts)
                    is_first = active_converts == 1 and not first_convert_entered.is_set()
                if is_first:
                    first_convert_entered.set()
                    release_first_convert.wait(timeout=5)
                else:
                    second_convert_entered.set()
                with active_lock:
                    active_converts -= 1
                return [SimpleNamespace(ts_init=1)]

        converter = Converter()
        btc_pipeline = BinanceVisionPipeline(catalog_path=tmp_path / "btc")
        btc_pipeline.downloader = Downloader(
            btc_task,
            _csv_payload("BTCUSDT-trades-2025-01-01.csv", b"1,65000,0.1,6500,1704067200000,true\n"),
            btc_downloaded,
        )
        eth_pipeline = BinanceVisionPipeline(catalog_path=tmp_path / "eth")
        eth_pipeline.downloader = Downloader(
            eth_task,
            _csv_payload("ETHUSDT-trades-2025-01-01.csv", b"1,3500,0.2,700,1704067200000,false\n"),
            eth_downloaded,
        )

        for pipeline in (btc_pipeline, eth_pipeline):
            pipeline._convert_workers = 1
            monkeypatch.setattr(pipeline, "_get_instrument", lambda _symbol: object())
            monkeypatch.setattr(pipeline, "_build_converter_kwargs", lambda *_args: {})
            monkeypatch.setattr(pipeline, "_write_objects", MagicMock(return_value=["memory://catalog/day.parquet"]))
            monkeypatch.setattr(pipeline, "_consolidate_catalog_data", MagicMock())
            monkeypatch.setattr(pipeline, "_detect_gaps_for_backfill", MagicMock(return_value=[]))
            monkeypatch.setattr(pipeline, "_catalog_storage_stats", MagicMock(return_value=(1, 1)))
            pipeline._update_db_catalog = AsyncMock()

        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: converter)
        monkeypatch.setattr(
            "tinohelm.data.pipeline.plan_catalog_missing_slices",
            lambda **_kwargs: [(date(2025, 1, 1), date(2025, 1, 1))],
        )

        btc_ingest = asyncio.create_task(btc_pipeline.ingest(
            symbol="BTCUSDT-PERP",
            data_type="trades",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        ))
        eth_ingest = asyncio.create_task(eth_pipeline.ingest(
            symbol="ETHUSDT-PERP",
            data_type="trades",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        ))

        await asyncio.to_thread(first_convert_entered.wait)
        await asyncio.wait_for(
            asyncio.gather(btc_downloaded.wait(), eth_downloaded.wait()),
            timeout=1,
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.to_thread(second_convert_entered.wait), timeout=0.2)

        release_first_convert.set()
        btc_result, eth_result = await asyncio.gather(btc_ingest, eth_ingest)

        assert btc_result.objects_count == 1
        assert eth_result.objects_count == 1
        assert max_active_converts == 1

    @pytest.mark.asyncio
    async def test_gap_backfill_shares_same_global_convert_write_gate(self, tmp_path: Path, monkeypatch):
        main_task = SimpleNamespace(
            url="memory://main",
            granularity="daily",
            dest_path=Path("BTCUSDT-1m-2025-01-01.csv"),
        )
        gap_task = SimpleNamespace(
            url="memory://gap",
            granularity="daily",
            dest_path=Path("BTCUSDT-1m-2025-01-02.csv"),
        )
        other_task = SimpleNamespace(
            url="memory://other",
            granularity="daily",
            dest_path=Path("ETHUSDT-trades-2025-01-01.csv"),
        )
        backfill_entered = threading.Event()
        release_backfill = threading.Event()
        other_downloaded = asyncio.Event()
        other_convert_entered = threading.Event()
        main_convert_calls = 0

        class MainDownloader:
            concurrency = 1

            def plan_downloads(self, **kwargs):
                if kwargs["start"] == date(2025, 1, 2):
                    return [gap_task]
                return [main_task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-1m.csv", b"1,2\n")

        class OtherDownloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [other_task]

            async def execute_task(self, _task):
                other_downloaded.set()
                return _csv_payload("ETHUSDT-trades.csv", b"1,2\n")

        main_pipeline = BinanceVisionPipeline(catalog_path=tmp_path / "main")
        main_pipeline.downloader = MainDownloader()
        main_pipeline._convert_workers = 1
        monkeypatch.setattr(main_pipeline, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(main_pipeline, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(main_pipeline, "_write_objects", MagicMock(return_value=["memory://catalog/main.parquet"]))
        monkeypatch.setattr(main_pipeline, "_catalog_storage_stats", MagicMock(return_value=(2, 2)))
        monkeypatch.setattr(main_pipeline, "_consolidate_catalog_data", MagicMock())
        monkeypatch.setattr(main_pipeline, "_detect_gaps_for_backfill", MagicMock(side_effect=[[(date(2025, 1, 2), date(2025, 1, 2))], []]))
        main_pipeline._update_db_catalog = AsyncMock()

        other_pipeline = BinanceVisionPipeline(catalog_path=tmp_path / "other")
        other_pipeline.downloader = OtherDownloader()
        other_pipeline._convert_workers = 1
        monkeypatch.setattr(other_pipeline, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(other_pipeline, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr(other_pipeline, "_write_objects", MagicMock(return_value=["memory://catalog/other.parquet"]))
        monkeypatch.setattr(other_pipeline, "_catalog_storage_stats", MagicMock(return_value=(1, 1)))
        monkeypatch.setattr(other_pipeline, "_consolidate_catalog_data", MagicMock())
        monkeypatch.setattr(other_pipeline, "_detect_gaps_for_backfill", MagicMock(return_value=[]))
        other_pipeline._update_db_catalog = AsyncMock()

        monkeypatch.setattr(
            "tinohelm.data.pipeline.plan_catalog_missing_slices",
            lambda **_kwargs: [(date(2025, 1, 1), date(2025, 1, 1))],
        )
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: object())

        def main_convert(*_args, **_kwargs):
            nonlocal main_convert_calls
            main_convert_calls += 1
            if main_convert_calls == 2:
                backfill_entered.set()
                release_backfill.wait(timeout=5)
            return (1, ["memory://catalog/main.parquet"])

        def other_convert(*_args, **_kwargs):
            other_convert_entered.set()
            return (1, ["memory://catalog/other.parquet"])

        monkeypatch.setattr(main_pipeline, "_convert_one_file", main_convert)
        monkeypatch.setattr(other_pipeline, "_convert_one_file", other_convert)

        main_ingest = asyncio.create_task(main_pipeline.ingest(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            interval="1m",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        ))

        await asyncio.to_thread(backfill_entered.wait)

        other_ingest = asyncio.create_task(other_pipeline.ingest(
            symbol="ETHUSDT-PERP",
            data_type="trades",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        ))

        await asyncio.wait_for(other_downloaded.wait(), timeout=1)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.to_thread(other_convert_entered.wait), timeout=0.2)

        release_backfill.set()
        main_result, other_result = await asyncio.gather(main_ingest, other_ingest)

        assert main_result.objects_count == 2
        assert other_result.objects_count == 1


class TestDailyTickIngest:
    @pytest.mark.asyncio
    async def test_trades_ingest_skips_consolidation_and_gap_detection(self, tmp_path: Path, monkeypatch):
        task = SimpleNamespace(url="memory://one", granularity="daily", dest_path=Path("BTCUSDT-trades-2025-01-01.csv"))

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-trades-2025-01-01.csv", b"1,2\n")

        class Converter:
            
            def validate_schema(self, df):
                assert list(df.columns) == [0, 1]

            def convert(self, df, instrument, **kwargs):
                return [tuple(df.iloc[0].tolist())]

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = Downloader()
        p._convert_workers = 1
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        monkeypatch.setattr("tinohelm.data.coverage.plan_catalog_missing_slices", lambda **_kwargs: [(date(2025, 1, 1), date(2025, 1, 1))])
        monkeypatch.setattr(p, "_write_objects", MagicMock(return_value=["memory://catalog/day.parquet"]))
        monkeypatch.setattr(p, "_consolidate_catalog_data", MagicMock())
        monkeypatch.setattr(p, "_detect_gaps_for_backfill", MagicMock(return_value=[]))
        monkeypatch.setattr(p, "_catalog_storage_stats", MagicMock(return_value=(1, 1)))
        p._update_db_catalog = AsyncMock()

        result = await p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="trades",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )

        assert result.objects_count == 1
        p._consolidate_catalog_data.assert_not_called()
        p._detect_gaps_for_backfill.assert_not_called()
        p._update_db_catalog.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bookticker_ingest_skips_consolidation_and_gap_detection(self, tmp_path: Path, monkeypatch):
        task = SimpleNamespace(url="memory://one", granularity="daily", dest_path=Path("BTCUSDT-bookTicker-2025-01-01.csv"))

        class Downloader:
            concurrency = 1

            def plan_downloads(self, **_kwargs):
                return [task]

            async def execute_task(self, _task):
                return _csv_payload("BTCUSDT-bookTicker-2025-01-01.csv", b"a,b\n1,2\n")

        class Converter:
            
            def validate_schema(self, df):
                assert list(df.columns) == ["a", "b"]

            def convert(self, df, instrument, **kwargs):
                return [tuple(df.iloc[0].tolist())]

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = Downloader()
        p._convert_workers = 1
        monkeypatch.setattr(p, "_get_instrument", lambda _symbol: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_args: {})
        monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())
        monkeypatch.setattr("tinohelm.data.coverage.plan_catalog_missing_slices", lambda **_kwargs: [(date(2025, 1, 1), date(2025, 1, 1))])
        monkeypatch.setattr(p, "_write_objects", MagicMock(return_value=["memory://catalog/day.parquet"]))
        monkeypatch.setattr(p, "_consolidate_catalog_data", MagicMock())
        monkeypatch.setattr(p, "_detect_gaps_for_backfill", MagicMock(return_value=[]))
        monkeypatch.setattr(p, "_catalog_storage_stats", MagicMock(return_value=(1, 1)))
        p._update_db_catalog = AsyncMock()

        result = await p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="bookTicker",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        )

        assert result.objects_count == 1
        p._consolidate_catalog_data.assert_not_called()
        p._detect_gaps_for_backfill.assert_not_called()
        p._update_db_catalog.assert_awaited_once()


class TestIngestEarlyFailClosed:
    def test_empty_csv_payload_fails_before_catalog_db_update(self, tmp_path: Path, monkeypatch):
        task = SimpleNamespace(url="memory://empty.csv")
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-trades-empty.csv", b""))

        class Converter:
            pass

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
            pass

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

class TestFundingRateCoverageShortCircuit:
    """The pipeline must skip plan_downloads when the unified coverage planner
    reports no missing funding-rate slices.
    """

    def test_ingest_skips_when_funding_rate_catalog_covers_range(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.side_effect = AssertionError(
            "plan_downloads must not run when funding-rate catalog covers range"
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

    def test_ingest_funding_rate_partial_coverage_plans_missing_slice(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [
                (date(2024, 1, 11), date(2024, 1, 11)),
            ],
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
        mock_dl.plan_downloads.assert_called_once_with(
            data_type="fundingRate",
            symbol="BTCUSDT-PERP",
            asset_class="um",
            start=date(2024, 1, 11),
            end=date(2024, 1, 11),
            interval=None,
        )

    def test_ingest_funding_rate_full_requested_slice_plans_downloads(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [
                (start, end),
            ],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")
        mock_dl = MagicMock()
        mock_dl.plan_downloads.return_value = []  # no tasks → skipped after planning
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

        mock_dl.plan_downloads.assert_called_once_with(
            data_type="fundingRate",
            symbol="BTCUSDT-PERP",
            asset_class="um",
            start=date(2024, 1, 1),
            end=date(2024, 1, 5),
            interval=None,
        )


class TestGeneralCoverageShortCircuit:
    def test_ingest_skips_when_bar_catalog_covers_range(
        self, monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda self, symbol, data_type, interval, start, end, source_type=None: [],
        )

        p = BinanceVisionPipeline(catalog_path="/tmp/test_catalog")

        mock_dl = MagicMock()
        mock_dl.plan_downloads.side_effect = AssertionError(
            "plan_downloads must not run when bar catalog covers range"
        )
        p.downloader = mock_dl

        async def _noop(*a, **kw):
            pass

        with patch.object(p, "_update_db_catalog", side_effect=_noop):
            result = asyncio.run(p.ingest(
                symbol="BTCUSDT-PERP",
                data_type="klines",
                interval="1m",
                start=date(2024, 1, 5),
                end=date(2024, 1, 20),
            ))

        assert result.skipped is True
        assert result.objects_count == 0
        mock_dl.plan_downloads.assert_not_called()


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
        assert consolidate_ranges == []

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
        assert consolidate_ranges == []

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

    def test_ingest_trade_tick_does_not_backfill_detected_gaps(self, monkeypatch: pytest.MonkeyPatch):
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


class TestConsolidationFailureDoesNotBlockIngest:
    """Consolidation is a post-write optimization; failure must not mark the job failed."""

    def test_consolidation_error_still_returns_result(self, tmp_path, monkeypatch):
        """When consolidation raises, ingest returns a successful result (data is safe)."""
        task = SimpleNamespace(url="memory://data.csv")
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(
            return_value=_csv_payload("BTCUSDT-1m-2025-01-01.csv", b"open_time,open,high,low,close,volume,close_time\n")
        )

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        monkeypatch.setattr(p, "_get_instrument", lambda _s: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_a: {})
        monkeypatch.setattr(
            "tinohelm.data.pipeline.get_converter",
            lambda _dt: SimpleNamespace(),
        )
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda *_a, **_kw: [(date(2025, 1, 1), date(2025, 1, 1))],
        )
        monkeypatch.setattr(p, "_convert_one_file", lambda *_a, **_kw: (100, ["/some/path.parquet"]))
        monkeypatch.setattr(
            p, "_consolidate_catalog_data",
            MagicMock(side_effect=RuntimeError("OOM during consolidation")),
        )
        monkeypatch.setattr(p, "_detect_gaps_for_backfill", MagicMock(return_value=[]))
        monkeypatch.setattr(p, "_catalog_storage_stats", MagicMock(return_value=(100, 5000)))
        p._update_db_catalog = AsyncMock()

        result = asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            interval="1m",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
        ))

        assert result.objects_count == 100
        assert not result.skipped
        p._consolidate_catalog_data.assert_called_once()
        p._update_db_catalog.assert_awaited_once()

    def test_consolidation_error_progress_shows_warning(self, tmp_path, monkeypatch):
        """Progress callback receives a message indicating consolidation failed but data is safe."""
        task = SimpleNamespace(url="memory://data.csv")
        mock_dl = MagicMock()
        mock_dl.concurrency = 1
        mock_dl.plan_downloads.return_value = [task]
        mock_dl.execute_task = AsyncMock(
            return_value=_csv_payload("BTCUSDT-1m-2025-01-01.csv", b"open_time,open,high,low,close,volume,close_time\n")
        )

        p = BinanceVisionPipeline(catalog_path=tmp_path)
        p.downloader = mock_dl
        p._convert_workers = 1
        monkeypatch.setattr(p, "_get_instrument", lambda _s: object())
        monkeypatch.setattr(p, "_build_converter_kwargs", lambda *_a: {})
        monkeypatch.setattr(
            "tinohelm.data.pipeline.get_converter",
            lambda _dt: SimpleNamespace(),
        )
        monkeypatch.setattr(
            "tinohelm.data.catalog.CatalogSession.missing_date_slices",
            lambda *_a, **_kw: [(date(2025, 1, 1), date(2025, 1, 1))],
        )
        monkeypatch.setattr(p, "_convert_one_file", lambda *_a, **_kw: (100, ["/some/path.parquet"]))
        monkeypatch.setattr(
            p, "_consolidate_catalog_data",
            MagicMock(side_effect=RuntimeError("OOM during consolidation")),
        )
        monkeypatch.setattr(p, "_detect_gaps_for_backfill", MagicMock(return_value=[]))
        monkeypatch.setattr(p, "_catalog_storage_stats", MagicMock(return_value=(100, 5000)))
        p._update_db_catalog = AsyncMock()

        progress_messages: list[tuple[int, str]] = []

        async def _track_progress(pct, msg):
            progress_messages.append((pct, msg))

        result = asyncio.run(p.ingest(
            symbol="BTCUSDT-PERP",
            data_type="klines",
            interval="1m",
            start=date(2025, 1, 1),
            end=date(2025, 1, 1),
            progress_cb=_track_progress,
        ))

        assert result.objects_count == 100
        warning_msgs = [m for _, m in progress_messages if "整理失败" in m]
        assert warning_msgs, f"Expected a consolidation warning, got: {progress_messages}"
