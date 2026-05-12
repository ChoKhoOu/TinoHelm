from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tinohelm.data.pipeline import BinanceVisionPipeline
from tinohelm.data.downloader import VisionCsvPayload


def _csv_payload(name: str, content: bytes) -> VisionCsvPayload:
    from io import BytesIO

    file_obj = BytesIO()
    file_obj.write(content)
    file_obj.seek(0)
    return VisionCsvPayload(name=name, file=file_obj)


def test_pipeline_commits_staged_outputs_before_db_catalog_update(monkeypatch, tmp_path: Path) -> None:
    task = SimpleNamespace(
        url="memory://one.csv",
        granularity="daily",
        dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"),
    )
    mock_dl = MagicMock()
    mock_dl.concurrency = 1
    mock_dl.plan_downloads.return_value = [task]
    mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n"))
    events: list[str] = []

    class Converter:
        supports_chunked = False

        def validate_schema(self, df):
            assert list(df.columns) == ["a", "b"]

        def convert(self, df, instrument, **kwargs):
            return [SimpleNamespace(ts_init=1)]

    async def update_catalog(*_args, **_kwargs):
        events.append("db_update")

    p = BinanceVisionPipeline(catalog_path=tmp_path)
    p.downloader = mock_dl
    p._convert_workers = 1
    p._clean_overlapping_parquet = MagicMock(return_value=None)
    p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
    p._write_objects = MagicMock(return_value=[str(tmp_path / "data" / "trade_tick" / "BTCUSDT-PERP.BINANCE" / "part.parquet")])
    p._written_file_size = MagicMock(return_value=123)
    p._commit_staged_outputs = MagicMock(side_effect=lambda **_kwargs: events.append("commit") or [])
    p._update_db_catalog = AsyncMock(side_effect=update_catalog)
    monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())

    asyncio.run(p.ingest(
        symbol="BTCUSDT-PERP",
        data_type="aggTrades",
        start=date(2025, 1, 1),
        end=date(2025, 1, 1),
    ))

    assert events == ["commit", "db_update"]


def test_pipeline_uses_ingest_staging_root_for_intermediate_writes(monkeypatch, tmp_path: Path) -> None:
    task = SimpleNamespace(
        url="memory://one.csv",
        granularity="daily",
        dest_path=Path("BTCUSDT-aggTrades-2025-01-01.csv"),
    )
    mock_dl = MagicMock()
    mock_dl.concurrency = 1
    mock_dl.plan_downloads.return_value = [task]
    mock_dl.execute_task = AsyncMock(return_value=_csv_payload("BTCUSDT-aggTrades-2025-01-01.csv", b"a,b\n1,2\n"))
    observed_paths: list[str] = []

    class Converter:
        supports_chunked = False

        def validate_schema(self, df):
            assert list(df.columns) == ["a", "b"]

        def convert(self, df, instrument, **kwargs):
            return [SimpleNamespace(ts_init=1)]

    def write_objects(*_args, **_kwargs):
        observed_paths.append(str(p._active_catalog_root()))
        return [str(tmp_path / "data" / "trade_tick" / "BTCUSDT-PERP.BINANCE" / "part.parquet")]

    p = BinanceVisionPipeline(catalog_path=tmp_path)
    p.downloader = mock_dl
    p._convert_workers = 1
    p._clean_overlapping_parquet = MagicMock(return_value=None)
    p._get_instrument = MagicMock(return_value=SimpleNamespace(id="BTCUSDT-PERP.BINANCE"))
    p._write_objects = MagicMock(side_effect=write_objects)
    p._written_file_size = MagicMock(return_value=123)
    p._commit_staged_outputs = MagicMock(return_value=[])
    p._update_db_catalog = AsyncMock(return_value=None)
    monkeypatch.setattr("tinohelm.data.pipeline.get_converter", lambda _data_type: Converter())

    asyncio.run(p.ingest(
        symbol="BTCUSDT-PERP",
        data_type="aggTrades",
        start=date(2025, 1, 1),
        end=date(2025, 1, 1),
    ))

    assert len(observed_paths) == 1
    assert ".ingest-staging" in observed_paths[0]
    assert p._active_catalog_root() == str(tmp_path)
