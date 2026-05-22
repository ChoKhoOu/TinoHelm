"""Tests for tinohelm.data.instruments module.

All HTTP calls are mocked — no network access required.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from tinohelm.data.instruments import (
    _normalize_value_str,
    _precision_from_string,
    make_instrument,
    strip_to_binance_api_symbol,
)

# ---------------------------------------------------------------------------
# Shared fake exchangeInfo payload
# ---------------------------------------------------------------------------

_FAKE_EXCHANGE_INFO: dict = {
    "symbols": [
        {
            "symbol": "BTCUSDT",
            "contractType": "PERPETUAL",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "pricePrecision": 1,
            "quantityPrecision": 3,
            "requiredMarginPercent": "5.0000",
            "maintMarginPercent": "2.5000",
            "filters": [
                {
                    "filterType": "PRICE_FILTER",
                    "maxPrice": "4529764",
                    "minPrice": "556.80",
                    "tickSize": "0.10",
                },
                {
                    "filterType": "LOT_SIZE",
                    "maxQty": "1000",
                    "minQty": "0.001",
                    "stepSize": "0.001",
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "notional": "5",
                },
            ],
        },
    ],
    "fetched_at": 9999999999,
}


# ---------------------------------------------------------------------------
# 1. strip_to_binance_api_symbol
# ---------------------------------------------------------------------------

class TestStripToBinanceApiSymbol:
    def test_strip_perp(self):
        assert strip_to_binance_api_symbol("BTCUSDT-PERP") == "BTCUSDT"

    def test_strip_perp_with_venue(self):
        assert strip_to_binance_api_symbol("BTCUSDT-PERP.BINANCE") == "BTCUSDT"

    def test_strip_swap(self):
        assert strip_to_binance_api_symbol("ETHUSDT-SWAP") == "ETHUSDT"

    def test_strip_spot(self):
        assert strip_to_binance_api_symbol("SOLUSDT-SPOT.BINANCE") == "SOLUSDT"

    def test_strip_linear(self):
        assert strip_to_binance_api_symbol("BNBUSDT-LINEAR") == "BNBUSDT"

    def test_no_suffix(self):
        assert strip_to_binance_api_symbol("BTCUSDT") == "BTCUSDT"

    def test_only_venue(self):
        assert strip_to_binance_api_symbol("BTCUSDT.BINANCE") == "BTCUSDT"


# ---------------------------------------------------------------------------
# 2. _precision_from_string
# ---------------------------------------------------------------------------

class TestPrecisionFromString:
    def test_one_decimal(self):
        assert _precision_from_string("0.1") == 1

    def test_three_decimals(self):
        assert _precision_from_string("0.001") == 3

    def test_integer(self):
        assert _precision_from_string("1") == 0

    def test_trailing_zeros(self):
        assert _precision_from_string("0.10000000") == 1

    def test_four_decimals(self):
        assert _precision_from_string("0.0001") == 4

    def test_large_integer(self):
        assert _precision_from_string("10") == 0

    def test_two_decimals(self):
        assert _precision_from_string("0.01") == 2


# ---------------------------------------------------------------------------
# 3. make_instrument with real exchangeInfo data
# ---------------------------------------------------------------------------

class TestMakeInstrumentFromExchangeInfo:
    @patch("tinohelm.data.instruments.fetch_exchange_info")
    def test_real_params_applied(self, mock_fetch):
        mock_fetch.return_value = _FAKE_EXCHANGE_INFO
        instrument = make_instrument("BTCUSDT-PERP")
        assert instrument.price_precision == 1
        assert instrument.size_precision == 3
        # tick_size "0.10" normalized to precision 1 -> "0.1"
        assert str(instrument.price_increment) == "0.1"

    @patch("tinohelm.data.instruments.fetch_exchange_info")
    def test_margin_from_exchange(self, mock_fetch):
        mock_fetch.return_value = _FAKE_EXCHANGE_INFO
        instrument = make_instrument("BTCUSDT-PERP")
        # requiredMarginPercent "5.0000" -> 0.05
        assert instrument.margin_init == Decimal("0.05")
        # maintMarginPercent "2.5000" -> 0.025
        assert instrument.margin_maint == Decimal("0.025")

    @patch("tinohelm.data.instruments.fetch_exchange_info")
    def test_currencies_resolved(self, mock_fetch):
        mock_fetch.return_value = _FAKE_EXCHANGE_INFO
        instrument = make_instrument("BTCUSDT-PERP")
        assert str(instrument.base_currency) == "BTC"
        assert str(instrument.quote_currency) == "USDT"

    @patch("tinohelm.data.instruments.fetch_exchange_info")
    def test_custom_fees(self, mock_fetch):
        mock_fetch.return_value = _FAKE_EXCHANGE_INFO
        custom_maker = Decimal("0.0001")
        custom_taker = Decimal("0.0003")
        instrument = make_instrument(
            "BTCUSDT-PERP",
            maker_fee=custom_maker,
            taker_fee=custom_taker,
        )
        assert instrument.maker_fee == custom_maker
        assert instrument.taker_fee == custom_taker


# ---------------------------------------------------------------------------
# 4. make_instrument fallback behaviour
# ---------------------------------------------------------------------------

class TestMakeInstrumentFallback:
    @patch("tinohelm.data.instruments.fetch_exchange_info")
    def test_fallback_on_api_failure(self, mock_fetch):
        mock_fetch.side_effect = RuntimeError("network down")
        instrument = make_instrument("BTCUSDT-PERP")
        # Should still return a valid instrument using hardcoded defaults
        assert instrument is not None
        assert str(instrument.id) == "BTCUSDT-PERP.BINANCE"
        # Fallback BTC defaults: price_prec=1, size_prec=3
        assert instrument.price_precision == 1
        assert instrument.size_precision == 3

    @patch("tinohelm.data.instruments.fetch_exchange_info")
    def test_fallback_unknown_symbol(self, mock_fetch):
        # Return exchangeInfo that does NOT contain the requested symbol
        mock_fetch.return_value = {
            "symbols": [
                {
                    "symbol": "ETHUSDT",
                    "contractType": "PERPETUAL",
                    "baseAsset": "ETH",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "requiredMarginPercent": "5.0000",
                    "maintMarginPercent": "2.5000",
                    "filters": [],
                },
            ],
            "fetched_at": 9999999999,
        }
        instrument = make_instrument("BTCUSDT-PERP")
        # Falls back because BTCUSDT not in the response
        assert instrument is not None
        assert str(instrument.id) == "BTCUSDT-PERP.BINANCE"


# ---------------------------------------------------------------------------
# 5. _normalize_value_str
# ---------------------------------------------------------------------------

class TestNormalizeValueStr:
    def test_trim_trailing_zeros(self):
        assert _normalize_value_str("0.10000000", 1) == "0.1"

    def test_short_frac_kept(self):
        # frac "1" is shorter than precision 2 but _normalize_value_str
        # only slices existing chars — no zero-padding beyond what's there.
        assert _normalize_value_str("0.1", 2) == "0.1"

    def test_integer_with_precision(self):
        assert _normalize_value_str("556", 1) == "556.0"

    def test_integer_zero_precision(self):
        assert _normalize_value_str("1", 0) == "1"
