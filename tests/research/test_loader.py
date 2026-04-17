"""Tests for `tinohelm.research.loader` — pure helpers + Parquet/JSON IO loaders.

The loader has two layers of testable surface:

1. **Pure helpers** (no IO): ``_bar_type_dir``, ``_instrument_id``, the type-set
   constants ``_BAR_TYPES`` / ``_TICK_TYPES`` / ``_FUNDING_TYPES``, and the
   ``load_data`` dispatcher's routing logic.
2. **IO loaders**: ``load_bars``, ``load_trade_ticks``, ``load_funding_rates``,
   and ``check_availability``. These we test by writing real Parquet/JSON files
   into ``tmp_path`` so the column-rename + side-enum mapping is exercised end-
   to-end (no NT, just pyarrow + pandas).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tinohelm.research import loader as L


# ──────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────


class TestBarTypeDir:
    @pytest.mark.parametrize("interval,expected", [
        ("1m", "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"),
        ("5m", "BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"),
        ("15m", "BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL"),
        ("1h", "BTCUSDT-PERP.BINANCE-1-HOUR-LAST-EXTERNAL"),
        ("4h", "BTCUSDT-PERP.BINANCE-4-HOUR-LAST-EXTERNAL"),
        ("1d", "BTCUSDT-PERP.BINANCE-1-DAY-LAST-EXTERNAL"),
    ])
    def test_known_intervals(self, interval, expected):
        assert L._bar_type_dir("BTCUSDT-PERP", interval) == expected

    def test_unknown_interval_falls_back_to_one_minute(self):
        # Defensive: an unmapped interval defaults to 1 MINUTE (mult=1)
        assert L._bar_type_dir("BTCUSDT-PERP", "unknown") == "BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL"


class TestInstrumentId:
    def test_appends_binance_when_no_dot(self):
        assert L._instrument_id("BTCUSDT-PERP") == "BTCUSDT-PERP.BINANCE"

    def test_passes_through_when_already_qualified(self):
        assert L._instrument_id("BTCUSDT-PERP.BINANCE") == "BTCUSDT-PERP.BINANCE"

    def test_handles_alternate_venue_when_qualified(self):
        # Symbols with ANY dot are treated as already-qualified.
        assert L._instrument_id("ETHUSDT.OKX") == "ETHUSDT.OKX"


class TestTypeSetConstants:
    def test_bar_types_complete(self):
        # Lock the wire-format contract: any new vision type that maps to "bar" must
        # be added to this set or the dispatcher will reject it. Failing this test
        # is intentional — update both sides.
        assert L._BAR_TYPES == frozenset({
            "bar", "klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines",
        })

    def test_tick_types_complete(self):
        assert L._TICK_TYPES == frozenset({"trade_tick", "aggTrades", "trades"})

    def test_funding_types_complete(self):
        assert L._FUNDING_TYPES == frozenset({"funding_rate", "fundingRate"})

    def test_no_overlap_between_categories(self):
        # A vision type must belong to exactly one category.
        assert L._BAR_TYPES.isdisjoint(L._TICK_TYPES)
        assert L._BAR_TYPES.isdisjoint(L._FUNDING_TYPES)
        assert L._TICK_TYPES.isdisjoint(L._FUNDING_TYPES)


# ──────────────────────────────────────────────────────────────────────
# load_data dispatcher
# ──────────────────────────────────────────────────────────────────────


class TestLoadDataDispatcher:
    def test_unknown_data_type_raises_with_helpful_message(self):
        with pytest.raises(ValueError, match="Unsupported data_type"):
            L.load_data("BTCUSDT-PERP", "__nope__", catalog_path="/tmp/anything")

    def test_unknown_type_error_lists_supported_types(self):
        try:
            L.load_data("BTCUSDT-PERP", "__nope__", catalog_path="/tmp/anything")
        except ValueError as exc:
            msg = str(exc)
            for t in ("bar", "klines", "trade_tick", "funding_rate", "fundingRate"):
                assert t in msg

    def test_bar_alias_routes_to_load_bars(self, tmp_path, monkeypatch):
        called = {}
        def fake_load_bars(symbol, interval, start, end, catalog_path):
            called["sym"] = symbol
            called["interval"] = interval
            called["start"] = start
            called["end"] = end
            called["cat"] = catalog_path
            return pd.DataFrame()
        monkeypatch.setattr(L, "load_bars", fake_load_bars)

        for type_name in ("bar", "klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"):
            called.clear()
            L.load_data("ETHUSDT-PERP", type_name, "5m", "2024-01-01", "2024-02-01", str(tmp_path))
            assert called == {"sym": "ETHUSDT-PERP", "interval": "5m",
                              "start": "2024-01-01", "end": "2024-02-01", "cat": str(tmp_path)}

    def test_tick_alias_routes_to_load_trade_ticks(self, monkeypatch):
        called = {}
        monkeypatch.setattr(L, "load_trade_ticks", lambda *a, **kw: called.setdefault("hit", True))
        for type_name in ("trade_tick", "aggTrades", "trades"):
            called.clear()
            L.load_data("BTCUSDT-PERP", type_name)
            assert called["hit"] is True

    def test_funding_alias_routes_to_load_funding_rates(self, monkeypatch):
        called = {}
        monkeypatch.setattr(L, "load_funding_rates", lambda *a, **kw: called.setdefault("hit", True))
        for type_name in ("funding_rate", "fundingRate"):
            called.clear()
            L.load_data("BTCUSDT-PERP", type_name)
            assert called["hit"] is True

    def test_bar_dispatch_defaults_interval_to_1m_when_none(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(L, "load_bars", lambda symbol, interval, start, end, cat:
                            captured.setdefault("interval", interval))
        L.load_data("BTCUSDT-PERP", "bar")
        assert captured["interval"] == "1m"


# ──────────────────────────────────────────────────────────────────────
# load_bars (real Parquet file)
# ──────────────────────────────────────────────────────────────────────


def _write_bar_parquet(catalog: Path, symbol: str, interval: str, n: int = 100) -> Path:
    """Write a fake NT-style bar Parquet file under catalog/data/bar/<bar_type>/."""
    bar_dir = catalog / "data" / "bar" / L._bar_type_dir(symbol, interval)
    bar_dir.mkdir(parents=True, exist_ok=True)
    # NT-style schema: columns prefixed with bar id, ts_init in nanoseconds
    base_ts_ns = int(pd.Timestamp("2024-01-01").value)
    step_ns = 60 * 1_000_000_000  # 1-minute steps
    df = pd.DataFrame({
        f"open_{symbol}": np.linspace(100.0, 110.0, n),
        f"high_{symbol}": np.linspace(101.0, 111.0, n),
        f"low_{symbol}": np.linspace(99.0, 109.0, n),
        f"close_{symbol}": np.linspace(100.0, 110.0, n),
        f"volume_{symbol}": np.full(n, 1000.0),
        "ts_init": [base_ts_ns + i * step_ns for i in range(n)],
        "ts_event": [base_ts_ns + i * step_ns for i in range(n)],
    })
    out_path = bar_dir / "data.parquet"
    df.to_parquet(out_path)
    return out_path


class TestLoadBars:
    def test_raises_when_dir_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No bar data"):
            L.load_bars("BTCUSDT-PERP", "1m", catalog_path=tmp_path)

    def test_loads_and_renames_columns(self, tmp_path):
        _write_bar_parquet(tmp_path, "BTCUSDT-PERP", "1m", n=100)
        df = L.load_bars("BTCUSDT-PERP", "1m", catalog_path=tmp_path)
        # Columns normalized to OHLCV
        assert sorted(df.columns) == ["close", "high", "low", "open", "volume"]
        assert len(df) == 100

    def test_index_is_datetime_named_timestamp(self, tmp_path):
        _write_bar_parquet(tmp_path, "BTCUSDT-PERP", "1m", n=50)
        df = L.load_bars("BTCUSDT-PERP", "1m", catalog_path=tmp_path)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "timestamp"

    def test_filters_by_start_date(self, tmp_path):
        _write_bar_parquet(tmp_path, "BTCUSDT-PERP", "1m", n=100)
        df = L.load_bars("BTCUSDT-PERP", "1m", start="2024-01-01 00:30:00", catalog_path=tmp_path)
        # Original spans 100 minutes from 00:00; start at 00:30 keeps 70 rows
        assert len(df) == 70

    def test_filters_by_end_date(self, tmp_path):
        _write_bar_parquet(tmp_path, "BTCUSDT-PERP", "1m", n=100)
        df = L.load_bars("BTCUSDT-PERP", "1m", end="2024-01-01 00:30:00", catalog_path=tmp_path)
        # Inclusive end → 31 rows (00:00 through 00:30)
        assert len(df) == 31

    def test_columns_coerced_to_numeric(self, tmp_path):
        _write_bar_parquet(tmp_path, "BTCUSDT-PERP", "1m", n=20)
        df = L.load_bars("BTCUSDT-PERP", "1m", catalog_path=tmp_path)
        for col in ("open", "high", "low", "close", "volume"):
            assert pd.api.types.is_numeric_dtype(df[col])

    def test_rows_sorted_by_index(self, tmp_path):
        _write_bar_parquet(tmp_path, "BTCUSDT-PERP", "1m", n=30)
        df = L.load_bars("BTCUSDT-PERP", "1m", catalog_path=tmp_path)
        assert df.index.is_monotonic_increasing


# ──────────────────────────────────────────────────────────────────────
# load_trade_ticks (real Parquet file)
# ──────────────────────────────────────────────────────────────────────


def _write_tick_parquet(
    catalog: Path,
    symbol: str,
    aggressor_dtype: str = "object",
    n: int = 50,
) -> Path:
    """Write a fake NT-style trade_tick Parquet under catalog/data/trade_tick/<inst_id>/."""
    inst_id = L._instrument_id(symbol)
    tick_dir = catalog / "data" / "trade_tick" / inst_id
    tick_dir.mkdir(parents=True, exist_ok=True)
    base_ts_ns = int(pd.Timestamp("2024-01-01").value)
    step_ns = 1_000_000_000  # 1 sec apart

    if aggressor_dtype == "object":
        agg = ["BUYER" if i % 3 == 0 else "SELLER" for i in range(n)]
    elif aggressor_dtype == "int":
        agg = [1 if i % 3 == 0 else 2 for i in range(n)]
    else:
        agg = None

    df = pd.DataFrame({
        "price": np.linspace(100.0, 110.0, n),
        "size": np.full(n, 0.5),
        "ts_event": [base_ts_ns + i * step_ns for i in range(n)],
        "ts_init": [base_ts_ns + i * step_ns for i in range(n)],
        "trade_id": [str(i) for i in range(n)],
    })
    if agg is not None:
        df["aggressor_side"] = agg
    out_path = tick_dir / "data.parquet"
    df.to_parquet(out_path)
    return out_path


class TestLoadTradeTicks:
    def test_raises_when_dir_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="No trade tick data"):
            L.load_trade_ticks("BTCUSDT-PERP", catalog_path=tmp_path)

    def test_loads_basic_columns(self, tmp_path):
        _write_tick_parquet(tmp_path, "BTCUSDT-PERP", n=60)
        df = L.load_trade_ticks("BTCUSDT-PERP", catalog_path=tmp_path)
        assert set(df.columns) == {"price", "quantity", "side"}
        assert len(df) == 60

    def test_aggressor_side_string_buyer_to_plus_one(self, tmp_path):
        _write_tick_parquet(tmp_path, "BTCUSDT-PERP", aggressor_dtype="object", n=30)
        df = L.load_trade_ticks("BTCUSDT-PERP", catalog_path=tmp_path)
        # Indices 0,3,6,... → BUYER → +1; rest → SELLER → -1
        assert df["side"].iloc[0] == 1
        assert df["side"].iloc[1] == -1
        assert df["side"].iloc[3] == 1

    def test_aggressor_side_int_enum_mapping(self, tmp_path):
        _write_tick_parquet(tmp_path, "BTCUSDT-PERP", aggressor_dtype="int", n=30)
        df = L.load_trade_ticks("BTCUSDT-PERP", catalog_path=tmp_path)
        # 1 → +1, 2 → -1
        assert df["side"].iloc[0] == 1
        assert df["side"].iloc[1] == -1

    def test_missing_aggressor_side_defaults_to_zero(self, tmp_path):
        _write_tick_parquet(tmp_path, "BTCUSDT-PERP", aggressor_dtype="missing", n=30)
        df = L.load_trade_ticks("BTCUSDT-PERP", catalog_path=tmp_path)
        assert (df["side"] == 0).all()

    def test_index_is_datetime_named_timestamp(self, tmp_path):
        _write_tick_parquet(tmp_path, "BTCUSDT-PERP", n=20)
        df = L.load_trade_ticks("BTCUSDT-PERP", catalog_path=tmp_path)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "timestamp"

    def test_filters_by_date_range(self, tmp_path):
        _write_tick_parquet(tmp_path, "BTCUSDT-PERP", n=60)
        # 60 ticks at 1-sec apart starting 2024-01-01 00:00:00
        df = L.load_trade_ticks(
            "BTCUSDT-PERP",
            start="2024-01-01 00:00:10",
            end="2024-01-01 00:00:30",
            catalog_path=tmp_path,
        )
        # Inclusive on both ends: 21 ticks (10..30)
        assert len(df) == 21


# ──────────────────────────────────────────────────────────────────────
# load_funding_rates (real JSON file)
# ──────────────────────────────────────────────────────────────────────


def _write_funding_json(home: Path, symbol: str, n: int = 24) -> Path:
    """Write a fake funding-rate JSON file at home/.tino/data/funding_rates/<symbol>.json."""
    cache_dir = home / ".tino" / "data" / "funding_rates"
    cache_dir.mkdir(parents=True, exist_ok=True)
    base_ms = int(pd.Timestamp("2024-01-01").value // 1_000_000)
    step_ms = 8 * 60 * 60 * 1000  # 8 hours
    records = [
        {"funding_time_ms": base_ms + i * step_ms,
         "funding_rate": 0.0001 * (i + 1),
         "mark_price": 50000.0 + i}
        for i in range(n)
    ]
    cache_file = cache_dir / f"{symbol.lower()}.json"
    cache_file.write_text(json.dumps(records))
    return cache_file


class TestLoadFundingRates:
    def test_raises_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        with pytest.raises(FileNotFoundError, match="No funding rate data"):
            L.load_funding_rates("BTCUSDT-PERP")

    def test_raises_when_records_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        cache_dir = tmp_path / ".tino" / "data" / "funding_rates"
        cache_dir.mkdir(parents=True)
        (cache_dir / "btcusdt-perp.json").write_text("[]")
        with pytest.raises(FileNotFoundError, match="empty"):
            L.load_funding_rates("BTCUSDT-PERP")

    def test_loads_funding_rate_and_mark_price(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _write_funding_json(tmp_path, "BTCUSDT-PERP", n=10)
        df = L.load_funding_rates("BTCUSDT-PERP")
        assert "funding_rate" in df.columns
        assert "mark_price" in df.columns
        assert len(df) == 10

    def test_index_is_datetime(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _write_funding_json(tmp_path, "BTCUSDT-PERP", n=5)
        df = L.load_funding_rates("BTCUSDT-PERP")
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.name == "timestamp"
        assert df.index.is_monotonic_increasing

    def test_filters_by_date_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _write_funding_json(tmp_path, "BTCUSDT-PERP", n=24)  # 24 × 8h = 8 days
        df = L.load_funding_rates(
            "BTCUSDT-PERP",
            start="2024-01-02",
            end="2024-01-04",
        )
        # Records every 8h between Jan 2 00:00 and Jan 4 00:00 inclusive: 7 records
        assert 6 <= len(df) <= 8

    def test_lowercase_symbol_filename(self, tmp_path, monkeypatch):
        # The function must downcase the symbol when computing the file path.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        # Write under lowercase
        _write_funding_json(tmp_path, "BTCUSDT-PERP", n=3)
        # Query with uppercase
        df = L.load_funding_rates("BTCUSDT-PERP")
        assert len(df) == 3


# ──────────────────────────────────────────────────────────────────────
# check_availability
# ──────────────────────────────────────────────────────────────────────


class TestCheckAvailability:
    def test_returns_unavailable_when_bar_dir_missing(self, tmp_path):
        out = L.check_availability("BTCUSDT-PERP", "bar", "1m", catalog_path=tmp_path)
        assert out == {"available": False, "count": 0}

    def test_returns_full_metadata_for_existing_bars(self, tmp_path):
        _write_bar_parquet(tmp_path, "BTCUSDT-PERP", "1m", n=50)
        out = L.check_availability("BTCUSDT-PERP", "bar", "1m", catalog_path=tmp_path)
        assert out["available"] is True
        assert out["count"] == 50
        assert "start" in out and "end" in out
        # Start ≤ end
        assert pd.Timestamp(out["start"]) <= pd.Timestamp(out["end"])

    def test_returns_unavailable_when_tick_dir_missing(self, tmp_path):
        out = L.check_availability("BTCUSDT-PERP", "trade_tick", catalog_path=tmp_path)
        assert out == {"available": False, "count": 0}

    def test_returns_metadata_for_existing_ticks(self, tmp_path):
        _write_tick_parquet(tmp_path, "BTCUSDT-PERP", n=30)
        out = L.check_availability("BTCUSDT-PERP", "trade_tick", catalog_path=tmp_path)
        assert out["available"] is True
        assert out["count"] == 30

    def test_funding_rate_unavailable_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        out = L.check_availability("BTCUSDT-PERP", "funding_rate")
        assert out == {"available": False, "count": 0}

    def test_funding_rate_metadata_when_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        _write_funding_json(tmp_path, "BTCUSDT-PERP", n=12)
        out = L.check_availability("BTCUSDT-PERP", "funding_rate")
        assert out["available"] is True
        assert out["count"] == 12
        assert "start" in out and "end" in out

    def test_funding_rate_empty_file_returns_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        cache_dir = tmp_path / ".tino" / "data" / "funding_rates"
        cache_dir.mkdir(parents=True)
        (cache_dir / "btcusdt-perp.json").write_text("[]")
        out = L.check_availability("BTCUSDT-PERP", "funding_rate")
        assert out == {"available": False, "count": 0}

    def test_unknown_data_type_returns_unavailable(self, tmp_path):
        out = L.check_availability("BTCUSDT-PERP", "__nope__", catalog_path=tmp_path)
        assert out == {"available": False, "count": 0}
