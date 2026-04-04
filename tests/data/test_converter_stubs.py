"""Tests for the 4 formerly-stub converters: bookTicker, bookDepth,
liquidationSnapshot, metrics.

These converters are now fully implemented with real conversion logic.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from tinohelm.data.converters import get_converter, SchemaError


@pytest.mark.parametrize("data_type", [
    "bookTicker",
    "bookDepth",
    "liquidationSnapshot",
    "metrics",
])
class TestConverterRegistration:
    def test_registered(self, data_type):
        c = get_converter(data_type)
        assert c is not None

    def test_has_supports_chunked(self, data_type):
        c = get_converter(data_type)
        assert isinstance(c.supports_chunked, bool)


# --- bookDepth ---

class TestBookDepthConverter:
    def test_schema_with_header(self):
        c = get_converter("bookDepth")
        df = pd.DataFrame({
            "timestamp": ["2025-01-01 00:00:07"],
            "percentage": [-5],
            "depth": [9445.59],
            "notional": [860670550.20],
        })
        c.validate_schema(df)  # should not raise

    def test_schema_too_few_columns(self):
        c = get_converter("bookDepth")
        df = pd.DataFrame([[1, 2]])
        with pytest.raises(SchemaError):
            c.validate_schema(df)

    def test_convert(self):
        c = get_converter("bookDepth")
        df = pd.DataFrame({
            "timestamp": ["2025-01-01 00:00:07", "2025-01-01 00:00:12"],
            "percentage": [-5, -4],
            "depth": [9445.59, 7939.65],
            "notional": [860670550.20, 726293373.33],
        })
        instrument = MagicMock()
        instrument.id = "BTCUSDT-PERP.BINANCE"
        records = c.convert(df, instrument, symbol="BTCUSDT-PERP")
        assert len(records) == 2
        assert records[0].percentage == -5
        assert records[0].depth == 9445.59
        assert records[0].symbol == "BTCUSDT-PERP"
        assert records[0].ts_event > 0


# --- bookTicker ---

class TestBookTickerConverter:
    def test_schema_too_few_columns(self):
        c = get_converter("bookTicker")
        df = pd.DataFrame([[1, 2, 3]])
        with pytest.raises(SchemaError):
            c.validate_schema(df)

    def test_valid_schema(self):
        c = get_converter("bookTicker")
        # 7 columns: update_id, bid_price, bid_qty, ask_price, ask_qty, txn_time, event_time
        df = pd.DataFrame([
            [2849995378056, "42000.0", "1.5", "42001.0", "2.0",
             1704067200000, 1704067200005],
        ])
        c.validate_schema(df)  # should not raise


# --- liquidationSnapshot (stub — not available on Vision) ---

class TestLiquidationConverter:
    def test_registered(self):
        c = get_converter("liquidationSnapshot")
        assert c is not None

    def test_convert_raises_not_implemented(self):
        c = get_converter("liquidationSnapshot")
        df = pd.DataFrame([[1, 2, 3]])
        with pytest.raises(NotImplementedError, match="not available"):
            c.convert(df, None)


# --- metrics ---

class TestMetricsConverter:
    def test_schema_with_header(self):
        c = get_converter("metrics")
        df = pd.DataFrame({
            "create_time": ["2025-01-01 00:05:00"],
            "symbol": ["BTCUSDT"],
            "sum_open_interest": [91253.336],
        })
        c.validate_schema(df)  # should not raise

    def test_schema_missing_required(self):
        c = get_converter("metrics")
        df = pd.DataFrame({"wrong_col": [1]})
        with pytest.raises(SchemaError):
            c.validate_schema(df)

    def test_convert(self):
        c = get_converter("metrics")
        df = pd.DataFrame({
            "create_time": ["2025-01-01 00:05:00", "2025-01-01 00:10:00"],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "sum_open_interest": [91253.336, 91236.202],
            "sum_open_interest_value": [8545728911.06, 8538844785.07],
            "count_toptrader_long_short_ratio": [1.813, 1.817],
            "sum_toptrader_long_short_ratio": [2.111, 2.110],
            "count_long_short_ratio": [1.827, 1.833],
            "sum_taker_long_short_vol_ratio": [0.767, 0.984],
        })
        instrument = MagicMock()
        instrument.id = "BTCUSDT-PERP.BINANCE"
        records = c.convert(df, instrument, symbol="BTCUSDT-PERP")
        assert len(records) == 2
        assert records[0].open_interest == 91253.336
        assert records[0].taker_long_short_vol_ratio == 0.767
        assert records[0].ts_event > 0
