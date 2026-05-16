from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from tinohelm.data.catalog import (
    book_depth_parquet_path,
    metrics_parquet_path,
    write_book_depth_parquet,
    write_metrics_parquet,
)
from tinohelm.data.catalog_helpers import WRITABLE_CATEGORIES, resolve_catalog_path
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY


@dataclass
class _Obj:
    ts_event: int


@dataclass
class _Metrics:
    symbol: str
    open_interest: float
    open_interest_value: float
    toptrader_long_short_ratio_count: float
    toptrader_long_short_ratio_sum: float
    global_long_short_ratio: float
    taker_long_short_vol_ratio: float
    ts_event: int
    ts_init: int


@dataclass
class _BookDepth:
    symbol: str
    percentage: float
    depth: float
    notional: float
    ts_event: int
    ts_init: int


def test_book_ticker_resolves_to_base_path() -> None:
    assert WRITE_CATEGORY["bookTicker"] == "quote_tick"
    assert "quote_tick" in WRITABLE_CATEGORIES
    assert resolve_catalog_path("/tmp/cat", "bookTicker") == Path("/tmp/cat")


def test_write_objects_dispatches_book_ticker_to_quote_writer(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    from tinohelm.data.pipeline import BinanceVisionPipeline

    called = {}

    def fake_writer(*, ticks, symbol, catalog_path, source_type, storage=None):
        called.update({
            "ticks": ticks,
            "symbol": symbol,
            "catalog_path": catalog_path,
            "source_type": source_type,
            "storage": storage,
        })
        return ["quote-file.parquet"]

    monkeypatch.setattr("tinohelm.data.catalog.write_quote_ticks", fake_writer)
    pipe = BinanceVisionPipeline(catalog_path=tmp_path / "catalog")

    out = pipe._write_objects([_Obj(1)], "BTCUSDT-PERP", "bookTicker", None)

    assert out == ["quote-file.parquet"]
    assert called["ticks"] == [_Obj(1)]
    assert called["symbol"] == "BTCUSDT-PERP"
    assert Path(called["catalog_path"]) == tmp_path / "catalog"
    assert called["source_type"] == "bookTicker"
    assert called["storage"].provider == "local"


def test_metrics_parquet_merges_and_dedupes_by_ts(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    first = _Metrics("BTCUSDT-PERP", 10.0, 100.0, 1.1, 1.2, 1.3, 1.4, 1_000, 1_000)
    duplicate = _Metrics("BTCUSDT-PERP", 11.0, 110.0, 2.1, 2.2, 2.3, 2.4, 1_000, 1_000)
    later = _Metrics("BTCUSDT-PERP", 12.0, 120.0, 3.1, 3.2, 3.3, 3.4, 2_000, 2_000)

    path = write_metrics_parquet([first], "BTCUSDT-PERP", root)
    path = write_metrics_parquet([duplicate, later], "BTCUSDT-PERP", root)

    assert path == metrics_parquet_path("BTCUSDT-PERP", root)
    table = pl.read_parquet(path).to_dict(as_series=False)
    assert table["ts_event"] == [1_000, 2_000]
    assert table["open_interest"] == [11.0, 12.0]
    assert table["sum_open_interest"] == [11.0, 12.0]
    assert table["open_interest_value"] == [110.0, 120.0]


def test_book_depth_parquet_merges_and_dedupes_by_ts_and_percentage(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    first = _BookDepth("BTCUSDT-PERP", 0.5, 10.0, 100.0, 1_000, 1_000)
    duplicate = _BookDepth("BTCUSDT-PERP", 0.5, 20.0, 200.0, 1_000, 1_000)
    other_pct = _BookDepth("BTCUSDT-PERP", 1.0, 30.0, 300.0, 1_000, 1_000)

    path = write_book_depth_parquet([first], "BTCUSDT-PERP", root)
    path = write_book_depth_parquet([duplicate, other_pct], "BTCUSDT-PERP", root)

    assert path == book_depth_parquet_path("BTCUSDT-PERP", root)
    table = pl.read_parquet(path).to_dict(as_series=False)
    assert table["ts_event"] == [1_000, 1_000]
    assert table["percentage"] == [0.5, 1.0]
    assert table["depth"] == [20.0, 30.0]
    assert table["notional"] == [200.0, 300.0]


