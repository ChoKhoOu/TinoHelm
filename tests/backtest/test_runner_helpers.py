"""Tests for BacktestRunner helper functions and methods.

Validates pure-logic helpers that don't require a full NautilusTrader
engine: interval parsing, fee parsing, fill model selection, latency
model building.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

# Import conditionally — nautilus_trader may not be installed in CI.
try:
    from tinohelm.backtest.runner import BacktestRunner, _interval_to_minutes

    _HAS_NT = True
except ImportError:
    _HAS_NT = False

pytestmark = pytest.mark.skipif(not _HAS_NT, reason="nautilus_trader not installed")


# ────────────────────────────────────────────────────────────────────
# _interval_to_minutes
# ────────────────────────────────────────────────────────────────────


class TestIntervalToMinutes:
    """Tests for the module-level _interval_to_minutes helper."""

    def test_minutes_basic(self):
        assert _interval_to_minutes("1m") == 1

    def test_minutes_5(self):
        assert _interval_to_minutes("5m") == 5

    def test_minutes_15(self):
        assert _interval_to_minutes("15m") == 15

    def test_minutes_30(self):
        assert _interval_to_minutes("30m") == 30

    def test_hours_1(self):
        assert _interval_to_minutes("1h") == 60

    def test_hours_4(self):
        assert _interval_to_minutes("4h") == 240

    def test_hours_12(self):
        assert _interval_to_minutes("12h") == 720

    def test_days_1(self):
        assert _interval_to_minutes("1d") == 1440

    def test_seconds_below_60_rounds_up(self):
        # 30s → 30//60=0 → max(1,0)=1
        assert _interval_to_minutes("30s") == 1

    def test_seconds_60(self):
        assert _interval_to_minutes("60s") == 1

    def test_seconds_120(self):
        assert _interval_to_minutes("120s") == 2

    def test_invalid_returns_zero(self):
        assert _interval_to_minutes("invalid") == 0

    def test_empty_string(self):
        assert _interval_to_minutes("") == 0

    def test_unknown_unit(self):
        assert _interval_to_minutes("5x") == 0

    def test_no_number(self):
        assert _interval_to_minutes("m") == 0

    def test_case_insensitive_minutes(self):
        assert _interval_to_minutes("5M") == 5

    def test_case_insensitive_hours(self):
        assert _interval_to_minutes("1H") == 60

    def test_case_insensitive_days(self):
        assert _interval_to_minutes("1D") == 1440


# ────────────────────────────────────────────────────────────────────
# BacktestRunner._parse_fee
# ────────────────────────────────────────────────────────────────────


class TestParseFee:
    """Tests for the static _parse_fee method."""

    def test_plain_decimal(self):
        assert BacktestRunner._parse_fee("0.0002") == Decimal("0.0002")

    def test_percentage_conversion(self):
        result = BacktestRunner._parse_fee("0.02%")
        assert abs(float(result) - 0.0002) < 1e-10

    def test_whitespace_stripped(self):
        assert BacktestRunner._parse_fee("  0.0003  ") == Decimal("0.0003")

    def test_percentage_with_whitespace(self):
        result = BacktestRunner._parse_fee(" 0.04% ")
        assert abs(float(result) - 0.0004) < 1e-10

    def test_zero_plain(self):
        assert BacktestRunner._parse_fee("0") == Decimal("0")

    def test_zero_percent(self):
        result = BacktestRunner._parse_fee("0%")
        assert float(result) == 0.0

    def test_large_fee(self):
        result = BacktestRunner._parse_fee("1%")
        assert abs(float(result) - 0.01) < 1e-10

    def test_very_small_fee(self):
        assert BacktestRunner._parse_fee("0.00001") == Decimal("0.00001")


# ────────────────────────────────────────────────────────────────────
# BacktestRunner._build_latency_model
# ────────────────────────────────────────────────────────────────────


class TestBuildLatencyModel:
    """Tests for the static _build_latency_model method."""

    def test_default_creates_model(self):
        """Empty config → 30ms default latency model."""
        model = BacktestRunner._build_latency_model({})
        assert model is not None

    def test_custom_latency(self):
        model = BacktestRunner._build_latency_model({"latency_ms": 50})
        assert model is not None

    def test_zero_disables(self):
        model = BacktestRunner._build_latency_model({"latency_ms": 0})
        assert model is None

    def test_negative_disables(self):
        model = BacktestRunner._build_latency_model({"latency_ms": -1})
        assert model is None

    def test_one_ms(self):
        model = BacktestRunner._build_latency_model({"latency_ms": 1})
        assert model is not None

    def test_advanced_nanos_accepted(self):
        """Extra nanos fields should not cause errors."""
        model = BacktestRunner._build_latency_model({
            "latency_ms": 10,
            "insert_latency_nanos": 5000,
            "update_latency_nanos": 3000,
            "cancel_latency_nanos": 2000,
        })
        assert model is not None


# ────────────────────────────────────────────────────────────────────
# BacktestRunner._build_fill_model
# ────────────────────────────────────────────────────────────────────


class TestBuildFillModel:
    """Tests for the static _build_fill_model method."""

    def test_default_type(self):
        from nautilus_trader.backtest.models import FillModel

        model = BacktestRunner._build_fill_model({})
        assert isinstance(model, FillModel)

    def test_default_with_custom_probs(self):
        from nautilus_trader.backtest.models import FillModel

        model = BacktestRunner._build_fill_model({
            "prob_fill_on_limit": 0.8,
            "prob_slippage": 0.1,
        })
        assert isinstance(model, FillModel)

    def test_best_price_type(self):
        model = BacktestRunner._build_fill_model({"fill_model_type": "best_price"})
        assert model is not None
        assert type(model).__name__ == "BestPriceFillModel"

    def test_one_tick_slippage(self):
        model = BacktestRunner._build_fill_model(
            {"fill_model_type": "one_tick_slippage"},
        )
        assert type(model).__name__ == "OneTickSlippageFillModel"

    def test_fixed_slippage_alias(self):
        """fixed_slippage from UI maps to OneTickSlippageFillModel."""
        model = BacktestRunner._build_fill_model(
            {"fill_model_type": "fixed_slippage"},
        )
        assert type(model).__name__ == "OneTickSlippageFillModel"

    def test_probabilistic(self):
        model = BacktestRunner._build_fill_model(
            {"fill_model_type": "probabilistic"},
        )
        assert type(model).__name__ == "ProbabilisticFillModel"

    def test_unknown_type_falls_back_to_default(self):
        from nautilus_trader.backtest.models import FillModel

        model = BacktestRunner._build_fill_model(
            {"fill_model_type": "nonexistent"},
        )
        assert isinstance(model, FillModel)


# ────────────────────────────────────────────────────────────────────
# BacktestRunner constructor — multi-symbol / multi-interval
# ────────────────────────────────────────────────────────────────────


class TestRunnerMultiInstrumentInit:
    """Constructor edge cases for multi-instrument support."""

    def test_none_symbol_and_none_interval(self):
        """No symbol/interval → empty lists."""
        runner = BacktestRunner(strategy_path="x:X", config_path="x:XConfig")
        assert runner.symbols == []
        assert runner.intervals == []

    def test_symbols_kwarg_takes_precedence(self):
        """symbols= keyword wins over symbol=."""
        runner = BacktestRunner(
            strategy_path="x:X",
            config_path="x:XConfig",
            symbol="BTCUSDT-PERP",
            symbols=["ETHUSDT-PERP", "XRPUSDT-PERP"],
        )
        assert runner.symbols == ["ETHUSDT-PERP", "XRPUSDT-PERP"]

    def test_intervals_kwarg_takes_precedence(self):
        """intervals= keyword wins over interval=."""
        runner = BacktestRunner(
            strategy_path="x:X",
            config_path="x:XConfig",
            interval="5m",
            intervals=["1m", "1h"],
        )
        assert runner.intervals == ["1m", "1h"]

    def test_backward_compat_aliases(self):
        """self.symbol and self.interval point to first element."""
        runner = BacktestRunner(
            strategy_path="x:X",
            config_path="x:XConfig",
            symbols=["BTCUSDT-PERP", "ETHUSDT-PERP"],
            intervals=["5m", "1h"],
        )
        assert runner.symbol == "BTCUSDT-PERP"
        assert runner.interval == "5m"

    def test_data_type_default(self):
        runner = BacktestRunner(strategy_path="x:X", config_path="x:XConfig")
        assert runner.data_type == "klines"

    def test_data_type_custom(self):
        runner = BacktestRunner(
            strategy_path="x:X",
            config_path="x:XConfig",
            data_type="markPriceKlines",
        )
        assert runner.data_type == "markPriceKlines"


# ────────────────────────────────────────────────────────────────────
# BacktestRunner._build_strategy_bundle
# ────────────────────────────────────────────────────────────────────


class TestBuildStrategyBundle:
    """Tests for the _build_strategy_bundle helper."""

    def test_default_account_settings(self):
        runner = BacktestRunner(
            strategy_path="strat.py:MyStrat",
            config_path="strat.py:MyConfig",
            symbol="BTCUSDT-PERP",
            interval="5m",
        )
        bundle = runner._build_strategy_bundle()
        assert bundle.account.starting_balance == 10000
        assert bundle.account.leverage == 1
        assert bundle.account.currency == "USDT"

    def test_custom_account_via_params(self):
        runner = BacktestRunner(
            strategy_path="strat.py:MyStrat",
            config_path="strat.py:MyConfig",
            symbol="BTCUSDT-PERP",
            interval="5m",
            strategy_params={"starting_balance": 50000, "leverage": 5},
        )
        bundle = runner._build_strategy_bundle()
        assert bundle.account.starting_balance == 50000
        assert bundle.account.leverage == 5

    def test_explicit_bundle_returned_as_is(self):
        from tinohelm.portfolio.config import StrategyBundle, AccountSettings

        explicit = StrategyBundle(
            strategy_class="x:X",
            config_class="x:XConfig",
            symbols=["ETHUSDT-PERP"],
            interval="1h",
            params={},
            actors=[],
            account=AccountSettings(starting_balance=99999),
        )
        runner = BacktestRunner(
            strategy_path="ignored",
            config_path="ignored",
            strategy_bundle=explicit,
        )
        assert runner._build_strategy_bundle() is explicit
