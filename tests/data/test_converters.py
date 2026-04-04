"""Tests for tinohelm.data.converters — 7 implemented converters.

NT imports are guarded with pytest.importorskip where needed so the tests
run cleanly even in environments where nautilus_trader is not installed.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tinohelm.data.converters import CONVERTER_REGISTRY, SchemaError, get_converter


# ---------------------------------------------------------------------------
# 1. Registry
# ---------------------------------------------------------------------------

class TestConverterRegistry:
    def test_get_known_converter(self):
        c = get_converter("klines")
        assert c is not None

    def test_get_unknown_converter_raises(self):
        with pytest.raises(ValueError, match="Unknown data_type"):
            get_converter("nonexistent_xyz")

    def test_all_11_types_registered(self):
        expected = {
            "klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines",
            "aggTrades", "trades", "fundingRate",
            "bookTicker", "bookDepth", "liquidationSnapshot", "metrics",
        }
        assert expected == set(CONVERTER_REGISTRY.keys())

    def test_converter_protocol_attributes(self):
        """Every registered converter exposes supports_chunked, validate_schema, convert."""
        for name, c in CONVERTER_REGISTRY.items():
            assert hasattr(c, "supports_chunked"), f"{name} missing supports_chunked"
            assert hasattr(c, "validate_schema"), f"{name} missing validate_schema"
            assert hasattr(c, "convert"), f"{name} missing convert"
            assert hasattr(c, "convert_chunk"), f"{name} missing convert_chunk"


# ---------------------------------------------------------------------------
# 2. KlinesConverter
# ---------------------------------------------------------------------------

class TestKlinesConverter:
    def setup_method(self):
        self.c = get_converter("klines")

    def test_supports_chunked_false(self):
        assert self.c.supports_chunked is False

    def test_validate_schema_ok_12_columns(self):
        df = pd.DataFrame([[0] * 12])
        self.c.validate_schema(df)  # should not raise

    def test_validate_schema_ok_7_columns(self):
        df = pd.DataFrame([[0] * 7])
        self.c.validate_schema(df)  # minimum required

    def test_validate_schema_too_few_columns(self):
        df = pd.DataFrame([[0] * 6])
        with pytest.raises(SchemaError):
            self.c.validate_schema(df)

    def test_validate_schema_3_columns(self):
        df = pd.DataFrame([[1, 2, 3]])
        with pytest.raises(SchemaError):
            self.c.validate_schema(df)

    def test_convert_requires_bar_type_kwarg(self):
        # NT import needed for this; skip gracefully if not available
        pytest.importorskip("nautilus_trader")
        df = pd.DataFrame([
            [1704067200000, "42000.0", "42100.0", "41900.0", "42050.0", "100.0",
             1704067259999, "4200000.0", 500, "50.0", "2100000.0", "0"],
        ])
        with pytest.raises(ValueError, match="bar_type"):
            self.c.convert(df, MagicMock())

    def test_convert_with_mock_bar_type(self):
        pytest.importorskip("nautilus_trader.persistence.wranglers")
        df = pd.DataFrame([
            [1704067200000, "42000.0", "42100.0", "41900.0", "42050.0", "100.0",
             1704067259999, "4200000.0", 500, "50.0", "2100000.0", "0"],
            [1704067260000, "42050.0", "42150.0", "41950.0", "42100.0", "110.0",
             1704067319999, "4600000.0", 520, "55.0", "2310000.0", "0"],
        ])
        mock_bar_type = MagicMock()
        mock_instrument = MagicMock()

        # BarDataWrangler is a Cython class — patch the module attribute that
        # the converter imports at call time.
        mock_wrangler = MagicMock()
        mock_wrangler.process.return_value = ["bar1", "bar2"]

        with patch("nautilus_trader.persistence.wranglers.BarDataWrangler",
                   return_value=mock_wrangler):
            result = self.c.convert(df, mock_instrument, bar_type=mock_bar_type)

        assert result == ["bar1", "bar2"]

    def test_convert_empty_after_processing_returns_empty(self):
        pytest.importorskip("nautilus_trader.persistence.wranglers")
        # All NaN rows → empty after dropna
        df = pd.DataFrame([
            [1704067200000, "NaN", "NaN", "NaN", "NaN", "NaN",
             1704067259999, "0", 0, "0", "0", "0"],
        ])
        mock_bar_type = MagicMock()
        mock_instrument = MagicMock()

        mock_wrangler = MagicMock()
        mock_wrangler.process.return_value = []

        with patch("nautilus_trader.persistence.wranglers.BarDataWrangler",
                   return_value=mock_wrangler):
            result = self.c.convert(df, mock_instrument, bar_type=mock_bar_type)

        assert result == []


# ---------------------------------------------------------------------------
# 3. AggTradesConverter
# ---------------------------------------------------------------------------

class TestAggTradesConverter:
    def setup_method(self):
        self.c = get_converter("aggTrades")

    def test_supports_chunked_true(self):
        assert self.c.supports_chunked is True

    def test_validate_schema_ok(self):
        # 7 columns: agg_trade_id, price, qty, first_trade_id, last_trade_id,
        #            transact_time, is_buyer_maker
        df = pd.DataFrame([[12345, "42000.0", "0.5", 100, 105, 1704067200000, True]])
        self.c.validate_schema(df)  # should not raise

    def test_validate_schema_too_few_columns(self):
        df = pd.DataFrame([[1, 2, 3]])
        with pytest.raises(SchemaError):
            self.c.validate_schema(df)

    def test_validate_schema_exactly_7_ok(self):
        df = pd.DataFrame([[0] * 7])
        self.c.validate_schema(df)

    def test_convert_aggressor_side_buyer_when_not_buyer_maker(self):
        pytest.importorskip("nautilus_trader")
        from nautilus_trader.model.enums import AggressorSide

        df = pd.DataFrame([
            [100, "42000.0", "0.5", 90, 100, 1704067200000, False],  # is_buyer_maker=False → BUYER aggressor
        ])
        mock_instrument = MagicMock()
        mock_instrument.make_price.side_effect = lambda v: v
        mock_instrument.make_qty.side_effect = lambda v: v

        with pytest.MonkeyPatch().context() as mp:
            with patch("nautilus_trader.model.data.TradeTick") as mock_tick_cls:
                mock_tick_cls.return_value = MagicMock()
                result = self.c.convert(df, mock_instrument)

            call_kwargs = mock_tick_cls.call_args[1]
            assert call_kwargs["aggressor_side"] == AggressorSide.BUYER

    def test_convert_aggressor_side_seller_when_buyer_maker(self):
        pytest.importorskip("nautilus_trader")
        from nautilus_trader.model.enums import AggressorSide

        df = pd.DataFrame([
            [101, "42000.0", "0.5", 91, 101, 1704067200000, True],  # is_buyer_maker=True → SELLER aggressor
        ])
        mock_instrument = MagicMock()
        mock_instrument.make_price.side_effect = lambda v: v
        mock_instrument.make_qty.side_effect = lambda v: v

        with patch("nautilus_trader.model.data.TradeTick") as mock_tick_cls:
            mock_tick_cls.return_value = MagicMock()
            result = self.c.convert(df, mock_instrument)

        call_kwargs = mock_tick_cls.call_args[1]
        assert call_kwargs["aggressor_side"] == AggressorSide.SELLER

    def test_convert_ts_event_ms_to_ns(self):
        pytest.importorskip("nautilus_trader")

        df = pd.DataFrame([
            [200, "50000.0", "1.0", 200, 200, 1704067200000, False],
        ])
        mock_instrument = MagicMock()
        mock_instrument.make_price.side_effect = lambda v: v
        mock_instrument.make_qty.side_effect = lambda v: v

        with patch("nautilus_trader.model.data.TradeTick") as mock_tick_cls:
            mock_tick_cls.return_value = MagicMock()
            self.c.convert(df, mock_instrument)

        call_kwargs = mock_tick_cls.call_args[1]
        # 1704067200000 ms * 1_000_000 = ts_ns
        assert call_kwargs["ts_event"] == 1704067200000 * 1_000_000

    def test_convert_chunk_same_as_convert(self):
        pytest.importorskip("nautilus_trader")
        df = pd.DataFrame([
            [300, "42000.0", "0.5", 300, 300, 1704067200000, False],
        ])
        mock_instrument = MagicMock()
        mock_instrument.make_price.side_effect = lambda v: v
        mock_instrument.make_qty.side_effect = lambda v: v

        with patch("nautilus_trader.model.data.TradeTick") as mock_tick_cls:
            mock_tick_cls.return_value = MagicMock()
            r1 = self.c.convert(df.copy(), mock_instrument)

        with patch("nautilus_trader.model.data.TradeTick") as mock_tick_cls:
            mock_tick_cls.return_value = MagicMock()
            r2 = self.c.convert_chunk(df.copy(), mock_instrument)

        assert len(r1) == len(r2)


# ---------------------------------------------------------------------------
# 4. TradesConverter
# ---------------------------------------------------------------------------

class TestTradesConverter:
    def setup_method(self):
        self.c = get_converter("trades")

    def test_supports_chunked_true(self):
        assert self.c.supports_chunked is True

    def test_validate_schema_ok(self):
        # 6 columns: id, price, qty, quote_qty, time, is_buyer_maker
        df = pd.DataFrame([[1, "42000.0", "0.1", "4200.0", 1704067200000, False]])
        self.c.validate_schema(df)

    def test_validate_schema_too_few_columns(self):
        df = pd.DataFrame([[1, 2, 3, 4, 5]])  # only 5
        with pytest.raises(SchemaError):
            self.c.validate_schema(df)

    def test_different_schema_from_agg_trades(self):
        """trades CSV has 6 columns vs aggTrades 7 — schema reqs differ."""
        trades_c = get_converter("trades")
        agg_c = get_converter("aggTrades")
        # 6-column DF: valid for trades, invalid for aggTrades
        df_6col = pd.DataFrame([[0] * 6])
        trades_c.validate_schema(df_6col)  # OK
        with pytest.raises(SchemaError):
            agg_c.validate_schema(df_6col)  # needs 7


# ---------------------------------------------------------------------------
# 5. FundingRateConverter
# ---------------------------------------------------------------------------

class TestFundingRateConverter:
    def setup_method(self):
        self.c = get_converter("fundingRate")

    def test_supports_chunked_false(self):
        assert self.c.supports_chunked is False

    def test_validate_schema_ok(self):
        # 3 columns: calc_time, funding_interval_hours, last_funding_rate
        df = pd.DataFrame([[1704067200000, 8, 0.0001]])
        self.c.validate_schema(df)

    def test_validate_schema_too_few_columns(self):
        df = pd.DataFrame([[1704067200000, 8]])
        with pytest.raises(SchemaError):
            self.c.validate_schema(df)

    def test_convert_returns_binance_funding_rate_objects(self):
        from tinohelm.data.converters.funding_rate import BinanceFundingRate

        df = pd.DataFrame([
            [1704067200000, 8, 0.0001],
            [1704096000000, 8, 0.0002],
        ])
        mock_instrument = MagicMock()
        mock_instrument.id = "BTCUSDT-PERP.BINANCE"

        records = self.c.convert(df, mock_instrument, symbol="BTCUSDT-PERP")

        assert len(records) == 2
        assert isinstance(records[0], BinanceFundingRate)
        assert isinstance(records[1], BinanceFundingRate)

    def test_convert_funding_rate_value(self):
        df = pd.DataFrame([
            [1704067200000, 8, 0.0001],
        ])
        mock_instrument = MagicMock()
        mock_instrument.id = "BTCUSDT-PERP.BINANCE"
        records = self.c.convert(df, mock_instrument, symbol="BTCUSDT-PERP")

        assert records[0].funding_rate == pytest.approx(0.0001)

    def test_convert_funding_time_ms_preserved(self):
        df = pd.DataFrame([
            [1704067200000, 8, 0.0001],
        ])
        mock_instrument = MagicMock()
        mock_instrument.id = "BTCUSDT-PERP.BINANCE"
        records = self.c.convert(df, mock_instrument, symbol="BTCUSDT-PERP")

        assert records[0].funding_time_ms == 1704067200000

    def test_convert_ts_event_ms_to_ns(self):
        df = pd.DataFrame([
            [1704067200000, 8, 0.0001],
        ])
        mock_instrument = MagicMock()
        mock_instrument.id = "BTCUSDT-PERP.BINANCE"
        records = self.c.convert(df, mock_instrument, symbol="BTCUSDT-PERP")

        assert records[0].ts_event == 1704067200000 * 1_000_000
        assert records[0].ts_init == 1704067200000 * 1_000_000

    def test_convert_symbol_from_kwarg(self):
        df = pd.DataFrame([[1704067200000, 8, 0.00015]])
        mock_instrument = MagicMock()
        mock_instrument.id = "BTCUSDT-PERP.BINANCE"
        records = self.c.convert(df, mock_instrument, symbol="BTCUSDT-PERP")
        assert records[0].symbol == "BTCUSDT-PERP"

    def test_convert_symbol_fallback_to_instrument_id(self):
        df = pd.DataFrame([[1704067200000, 8, 0.0001]])
        mock_instrument = MagicMock()
        mock_instrument.id = "BTCUSDT-PERP.BINANCE"
        # No symbol kwarg → falls back to str(instrument.id)
        records = self.c.convert(df, mock_instrument)
        assert records[0].symbol == str(mock_instrument.id)

    def test_convert_empty_df_returns_empty_list(self):
        df = pd.DataFrame(columns=[0, 1, 2])
        mock_instrument = MagicMock()
        mock_instrument.id = "BTCUSDT-PERP.BINANCE"
        records = self.c.convert(df, mock_instrument, symbol="BTCUSDT-PERP")
        assert records == []

    def test_convert_multiple_rows(self):
        df = pd.DataFrame([
            [1704067200000, 8, 0.0001],
            [1704096000000, 8, -0.0002],
            [1704124800000, 8, 0.0003],
        ])
        mock_instrument = MagicMock()
        mock_instrument.id = "X"
        records = self.c.convert(df, mock_instrument, symbol="BTCUSDT-PERP")
        assert len(records) == 3
        assert records[1].funding_rate == pytest.approx(-0.0002)
