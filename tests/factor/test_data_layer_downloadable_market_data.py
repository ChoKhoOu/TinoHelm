from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import polars as pl

from tinohelm.data.catalog import book_depth_parquet_path, metrics_parquet_path
from tinohelm.data.catalog_helpers import resolve_catalog_path
from tinohelm.factor.data_layer import DataLayer
from tinohelm.factor.types import DataRequest
from tinohelm.factor.universe import Universe

T0 = 1_620_000_000_000_000_000
MIN = 60_000_000_000
SCALE = 10_000_000_000_000_000
SYMBOL = "BTCUSDT-PERP"
INSTRUMENT = "BTCUSDT-PERP.BINANCE"


def _fp(value: float) -> bytes:
    return int(round(value * SCALE)).to_bytes(16, "little", signed=True)


def _universe(tmp_path: Path) -> Universe:
    path = tmp_path / "universe.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol", "listing_date", "delisting_date"])
        writer.writeheader()
        writer.writerow({"symbol": SYMBOL, "listing_date": "2020-01-01", "delisting_date": ""})
    return Universe.load_csv(path)


def _loader(tmp_path: Path) -> DataLayer:
    return DataLayer(_universe(tmp_path), catalog_root=tmp_path / "catalog", max_workers=1)


def _write_quote_ticks(root: Path) -> None:
    quote_dir = resolve_catalog_path(root, "bookTicker") / "data" / "quote_tick" / INSTRUMENT
    quote_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [T0 + 10_000_000_000, T0 + 20_000_000_000, T0 + MIN + 5_000_000_000],
        "bid_price": [_fp(100.0), _fp(101.0), _fp(102.0)],
        "bid_size": [_fp(2.0), _fp(3.0), _fp(1.0)],
        "ask_price": [_fp(101.0), _fp(103.0), _fp(104.0)],
        "ask_size": [_fp(1.0), _fp(1.0), _fp(3.0)],
    }).write_parquet(quote_dir / "quotes.parquet")


def _write_trade_ticks(root: Path) -> None:
    trade_dir = resolve_catalog_path(root, "aggTrades") / "data" / "trade_tick" / INSTRUMENT
    trade_dir.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [T0 + 10_000_000_000, T0 + 20_000_000_000, T0 + MIN + 5_000_000_000],
        "price": [_fp(100.0), _fp(99.5), _fp(101.0)],
        "size": [_fp(2.0), _fp(1.0), _fp(4.0)],
        "aggressor_side": [1, 2, 1],
    }).write_parquet(trade_dir / "trades.parquet")


def test_quote_tick_fields_load_from_book_ticker_only(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    _write_quote_ticks(root)
    dl = _loader(tmp_path)

    panel = dl.load(
        DataRequest(symbol=SYMBOL, field_name="orderbook_imbalance", frequency="1m", source="quote_tick", lookback=0),
        start=datetime(2021, 5, 3, 0, 0),
        end=datetime(2021, 5, 3, 0, 1),
    )["orderbook_imbalance"]

    assert panel.columns == ["ts", SYMBOL]
    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    assert panel[SYMBOL].to_list() == [0.5]

    spread = dl.load(DataRequest(symbol=SYMBOL, field_name="spread_bps", frequency="1m", source="quote_tick", lookback=0))["spread_bps"]
    assert spread["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000), datetime(2021, 5, 3, 0, 1, 59, 999000)]
    assert spread[SYMBOL].round(6).to_list() == [196.078431, 194.174757]


def test_trade_tick_fields_bucket_without_lookahead(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    _write_trade_ticks(root)
    dl = _loader(tmp_path)

    panel = dl.load(
        DataRequest(symbol=SYMBOL, field_name="trade_imbalance", frequency="1m", source="trade_tick", lookback=0),
        start=datetime(2021, 5, 3, 0, 0),
        end=datetime(2021, 5, 3, 0, 1),
    )["trade_imbalance"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    assert panel[SYMBOL].to_list() == [1 / 3]

    buy_qty = dl.load(
        DataRequest(symbol=SYMBOL, field_name="buy_qty", frequency="1m", source="trade_tick", lookback=0),
        start=datetime(2021, 5, 3, 0, 0),
        end=datetime(2021, 5, 3, 0, 1),
    )["buy_qty"]
    assert buy_qty["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    assert buy_qty[SYMBOL].to_list() == [2.0]


def test_trade_tick_loads_from_resolved_source_root(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    _write_trade_ticks(root)
    dl = DataLayer(
        _universe(tmp_path),
        catalog_root=resolve_catalog_path(root, "aggTrades"),
        max_workers=1,
    )

    panel = dl.load(
        DataRequest(symbol=SYMBOL, field_name="trade_imbalance", frequency="1m", source="trade_tick", lookback=0),
        start=datetime(2021, 5, 3, 0, 0),
        end=datetime(2021, 5, 3, 0, 1),
    )["trade_imbalance"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000)]
    assert panel[SYMBOL].to_list() == [1 / 3]


def test_metrics_open_interest_fields_bucket_to_requested_frequency(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    path = metrics_parquet_path(SYMBOL, root)
    path.parent.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [T0, T0 + MIN],
        "open_interest": [10.0, 12.0],
        "sum_open_interest": [10.0, 12.0],
        "open_interest_value": [100.0, 120.0],
        "global_long_short_ratio": [1.5, 1.6],
    }).write_parquet(path)
    dl = _loader(tmp_path)

    panel = dl.load(DataRequest(symbol=SYMBOL, field_name="open_interest", frequency="5m", source="open_interest", lookback=0))["open_interest"]

    assert panel.columns == ["ts", SYMBOL]
    assert panel["ts"].to_list() == [datetime(2021, 5, 2, 23, 59, 59, 999000), datetime(2021, 5, 3, 0, 4, 59, 999000)]
    assert panel[SYMBOL].to_list() == [10.0, 12.0]


def test_book_depth_selects_lowest_percentage_per_timestamp_and_buckets(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    path = book_depth_parquet_path(SYMBOL, root)
    path.parent.mkdir(parents=True)
    pl.DataFrame({
        "ts_event": [T0 + 10_000_000_000, T0 + 10_000_000_000, T0 + MIN + 5_000_000_000],
        "percentage": [5.0, 0.5, 1.0],
        "depth": [500.0, 50.0, 100.0],
        "notional": [50_000.0, 5_000.0, 10_000.0],
    }).write_parquet(path)
    dl = _loader(tmp_path)

    panel = dl.load(
        DataRequest(symbol=SYMBOL, field_name="book_depth", frequency="1m", source="book_depth", lookback=0),
    )["book_depth"]

    assert panel["ts"].to_list() == [datetime(2021, 5, 3, 0, 0, 59, 999000), datetime(2021, 5, 3, 0, 1, 59, 999000)]
    assert panel[SYMBOL].to_list() == [50.0, 100.0]
