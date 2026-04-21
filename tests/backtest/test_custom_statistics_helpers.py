"""Tests for ``tinohelm.backtest.custom_statistics_helpers`` — all NT-free.

These helpers carry the actual math for the 17 ``PortfolioStatistic``
subclasses in :mod:`tinohelm.backtest.custom_statistics` — the classes
themselves are thin wrappers.  The contracts tested here are the
user-visible behaviour of the tearsheet statistics block; any silent
drift in rounding, boundary conditions, or NaN handling would change
what users see in backtest reports.

The tests deliberately avoid importing NautilusTrader so they can run
in lean CI images.  A ``sys.meta_path`` blocker fixture makes that
property explicit.
"""
from __future__ import annotations

import math
import sys

import pandas as pd
import pytest

from tinohelm.backtest import custom_statistics_helpers as H


# ────────────────────────────────────────────────────────────────────
# calc_max_drawdown_pct
# ────────────────────────────────────────────────────────────────────


class TestCalcMaxDrawdownPct:
    def test_none_returns_none(self):
        assert H.calc_max_drawdown_pct(None) is None

    def test_empty_series_returns_none(self):
        assert H.calc_max_drawdown_pct(pd.Series([], dtype=float)) is None

    def test_all_positive_returns_zero_drawdown(self):
        # Monotone-up equity ⇒ no drawdown ⇒ 0.0
        result = H.calc_max_drawdown_pct(pd.Series([0.01, 0.02, 0.03]))
        assert result == 0.0

    def test_single_drop_from_peak(self):
        # +10%, -20% ⇒ peak 1.10, trough 0.88 ⇒ dd = (0.88-1.10)/1.10 ≈ -0.2
        result = H.calc_max_drawdown_pct(pd.Series([0.10, -0.20]))
        assert result is not None
        assert result == pytest.approx(-0.2, abs=1e-6)

    def test_rounds_to_6_decimals(self):
        # Build a drawdown that would round beyond 6dp
        result = H.calc_max_drawdown_pct(pd.Series([0.001, -0.0001234567]))
        assert result is not None
        # Must have at most 6 decimal places after round()
        assert abs(result * 1_000_000 - round(result * 1_000_000)) < 1e-9

    def test_single_element_no_drawdown(self):
        # One return has no prior peak ⇒ cum == peak ⇒ dd = 0
        result = H.calc_max_drawdown_pct(pd.Series([0.05]))
        assert result == 0.0

    def test_all_nan_returns_none(self):
        # NaN propagates → dd.min() is NaN → None
        result = H.calc_max_drawdown_pct(pd.Series([math.nan, math.nan]))
        assert result is None


# ────────────────────────────────────────────────────────────────────
# calc_annual_return (CAGR)
# ────────────────────────────────────────────────────────────────────


class TestCalcAnnualReturn:
    def test_none_returns_none(self):
        assert H.calc_annual_return(None) is None

    def test_empty_returns_none(self):
        assert H.calc_annual_return(pd.Series([], dtype=float)) is None

    def test_single_observation_returns_none(self):
        # Needs at least 2 observations for CAGR to be meaningful
        assert H.calc_annual_return(pd.Series([0.05])) is None

    def test_negative_total_return_returns_none(self):
        # -100% wipeout ⇒ total_ret <= 0 ⇒ None (CAGR undefined on negative cum)
        assert H.calc_annual_return(pd.Series([-1.0, 0.0])) is None

    def test_exactly_zero_total_return_returns_none(self):
        # +100%, -50% ⇒ (1+1)*(1-0.5)=1.0? No: 1 * 2 * 0.5 = 1.0 (cum==1 means
        # total_ret = 1.0 which is > 0, so CAGR = 1 ** x - 1 = 0)
        # But a true zero requires (1 + r).cumprod() final == 0 -- use -1.0
        assert H.calc_annual_return(pd.Series([0.0, -1.0, 0.0])) is None

    def test_daily_returns_252_days_back_out_annual(self):
        # A flat 1% daily return for 252 days ⇒ cum = 1.01^252 ≈ 12.32
        # CAGR annualised to 252 days = total_ret ** (252/252) - 1 = 11.32
        r = pd.Series([0.01] * 252)
        result = H.calc_annual_return(r)
        expected = 1.01 ** 252 - 1
        assert result == pytest.approx(expected, rel=1e-5)

    def test_rounds_to_6_decimals(self):
        r = pd.Series([0.001234567] * 10)
        result = H.calc_annual_return(r)
        assert result is not None
        # Verify rounding to 6dp
        assert abs(result * 1_000_000 - round(result * 1_000_000)) < 1e-9


# ────────────────────────────────────────────────────────────────────
# calc_calmar_ratio
# ────────────────────────────────────────────────────────────────────


class TestCalcCalmarRatio:
    def test_none_returns_none(self):
        assert H.calc_calmar_ratio(None) is None

    def test_single_obs_returns_none(self):
        assert H.calc_calmar_ratio(pd.Series([0.05])) is None

    def test_negative_total_returns_none(self):
        assert H.calc_calmar_ratio(pd.Series([-1.0, 0.0])) is None

    def test_zero_drawdown_returns_none(self):
        # Monotone up ⇒ max_dd = 0 ⇒ None (division by zero)
        assert H.calc_calmar_ratio(pd.Series([0.01, 0.01, 0.01])) is None

    def test_happy_path_positive_ratio(self):
        # +10%, -5%, +5% ⇒ cum = [1.10, 1.045, 1.09725], peak 1.10, dd -5%
        result = H.calc_calmar_ratio(pd.Series([0.10, -0.05, 0.05]))
        assert result is not None
        assert result > 0  # CAGR positive / dd positive ⇒ positive ratio

    def test_rounds_to_4_decimals(self):
        result = H.calc_calmar_ratio(pd.Series([0.02, -0.01, 0.015]))
        assert result is not None
        # Verify 4-decimal rounding
        assert abs(result * 10_000 - round(result * 10_000)) < 1e-9


# ────────────────────────────────────────────────────────────────────
# calc_total_trades
# ────────────────────────────────────────────────────────────────────


class TestCalcTotalTrades:
    def test_none_returns_zero(self):
        assert H.calc_total_trades(None) == 0

    def test_empty_returns_zero(self):
        assert H.calc_total_trades(pd.Series([], dtype=float)) == 0

    def test_counts_all_entries(self):
        # Includes zero-PnL trades — total count ignores sign
        assert H.calc_total_trades(pd.Series([1.0, -2.0, 0.0, 3.5])) == 4


# ────────────────────────────────────────────────────────────────────
# calc_winning_trades / calc_losing_trades
# ────────────────────────────────────────────────────────────────────


class TestCalcWinningLosingTrades:
    def test_winning_none(self):
        assert H.calc_winning_trades(None) == 0

    def test_winning_empty(self):
        assert H.calc_winning_trades(pd.Series([], dtype=float)) == 0

    def test_winning_strict_positive(self):
        # 0 is NOT a winner (strict >)
        assert H.calc_winning_trades(pd.Series([1.0, 0.0, 2.0, -1.0])) == 2

    def test_winning_all_losers(self):
        assert H.calc_winning_trades(pd.Series([-1.0, -2.0])) == 0

    def test_losing_none(self):
        assert H.calc_losing_trades(None) == 0

    def test_losing_empty(self):
        assert H.calc_losing_trades(pd.Series([], dtype=float)) == 0

    def test_losing_strict_negative_excludes_zero(self):
        # Zero-PnL trades are NOT counted as losers in this helper
        # (GrossLoss uses a different boundary — see TestCalcGrossLoss).
        assert H.calc_losing_trades(pd.Series([-1.0, 0.0, 2.0, -3.0])) == 2

    def test_losing_returns_python_int_not_numpy(self):
        result = H.calc_losing_trades(pd.Series([-1.0, -2.0]))
        assert type(result) is int  # noqa: E721 — exact type match


# ────────────────────────────────────────────────────────────────────
# calc_gross_profit / calc_gross_loss
# ────────────────────────────────────────────────────────────────────


class TestCalcGrossProfitLoss:
    def test_profit_none(self):
        assert H.calc_gross_profit(None) == 0.0

    def test_profit_empty(self):
        assert H.calc_gross_profit(pd.Series([], dtype=float)) == 0.0

    def test_profit_sum_winners(self):
        assert H.calc_gross_profit(pd.Series([1.0, -2.0, 3.5, 0.0])) == 4.5

    def test_profit_rounds_to_4dp(self):
        result = H.calc_gross_profit(pd.Series([1.234567, 2.345678]))
        # Sum = 3.580245, rounded to 4dp = 3.5802
        assert result == 3.5802

    def test_loss_none(self):
        assert H.calc_gross_loss(None) == 0.0

    def test_loss_empty(self):
        assert H.calc_gross_loss(pd.Series([], dtype=float)) == 0.0

    def test_loss_boundary_includes_zero(self):
        # Zero-PnL trades go into the "loss" bucket (historical contract, <= 0).
        # Zero sums to zero, so the observable value is unchanged — but this
        # locks the boundary in case the filter is ever inverted.
        assert H.calc_gross_loss(pd.Series([1.0, -2.0, 0.0, -1.5])) == -3.5

    def test_loss_all_winners_returns_zero(self):
        # Series of purely positive PnLs → losers mask empty → sum() → 0.0
        assert H.calc_gross_loss(pd.Series([1.0, 2.0, 3.0])) == 0.0

    def test_loss_rounds_to_4dp(self):
        result = H.calc_gross_loss(pd.Series([-1.23456789]))
        # Rounded to 4dp = -1.2346
        assert result == -1.2346


# ────────────────────────────────────────────────────────────────────
# calc_avg_win_loss_ratio
# ────────────────────────────────────────────────────────────────────


class TestCalcAvgWinLossRatio:
    def test_none(self):
        assert H.calc_avg_win_loss_ratio(None) is None

    def test_empty(self):
        assert H.calc_avg_win_loss_ratio(pd.Series([], dtype=float)) is None

    def test_no_winners(self):
        assert H.calc_avg_win_loss_ratio(pd.Series([-1.0, -2.0])) is None

    def test_no_losers(self):
        assert H.calc_avg_win_loss_ratio(pd.Series([1.0, 2.0])) is None

    def test_only_zero_pnl_no_winners_no_losers(self):
        # Zero-PnL trades don't qualify as winners OR losers (strict comparisons)
        assert H.calc_avg_win_loss_ratio(pd.Series([0.0, 0.0])) is None

    def test_happy_path(self):
        # avg_win = 10, avg_loss = -5 ⇒ ratio = 10/5 = 2.0
        assert H.calc_avg_win_loss_ratio(pd.Series([10.0, -5.0])) == 2.0

    def test_multiple_winners_losers(self):
        # winners avg=(10+20)/2=15, losers avg=(-5-15)/2=-10, ratio=15/10=1.5
        assert H.calc_avg_win_loss_ratio(pd.Series([10.0, 20.0, -5.0, -15.0])) == 1.5

    def test_rounds_to_4dp(self):
        # 1/3 ratio  → 0.3333
        result = H.calc_avg_win_loss_ratio(pd.Series([1.0, -3.0]))
        assert result == 0.3333


# ────────────────────────────────────────────────────────────────────
# consecutive-streak helpers
# ────────────────────────────────────────────────────────────────────


class TestConsecutiveStreaks:
    def test_wins_none(self):
        assert H.calc_max_consecutive_wins(None) == 0

    def test_wins_empty(self):
        assert H.calc_max_consecutive_wins(pd.Series([], dtype=float)) == 0

    def test_wins_simple_streak(self):
        assert H.calc_max_consecutive_wins(pd.Series([1.0, 2.0, -1.0, 3.0])) == 2

    def test_wins_longest_in_middle(self):
        assert H.calc_max_consecutive_wins(pd.Series([1.0, -1.0, 1.0, 1.0, 1.0, -1.0, 1.0])) == 3

    def test_wins_all_positive(self):
        assert H.calc_max_consecutive_wins(pd.Series([1.0, 2.0, 3.0, 4.0])) == 4

    def test_wins_zero_breaks_streak(self):
        # Zero resets the streak (strict > 0, historical behaviour)
        assert H.calc_max_consecutive_wins(pd.Series([1.0, 1.0, 0.0, 1.0])) == 2

    def test_losses_none(self):
        assert H.calc_max_consecutive_losses(None) == 0

    def test_losses_simple(self):
        assert H.calc_max_consecutive_losses(pd.Series([-1.0, -1.0, 1.0, -1.0])) == 2

    def test_losses_zero_breaks_streak(self):
        # Same reset behaviour — zero-PnL interrupts a losing streak
        assert H.calc_max_consecutive_losses(pd.Series([-1.0, -1.0, 0.0, -1.0])) == 2

    def test_losses_all_negative(self):
        assert H.calc_max_consecutive_losses(pd.Series([-1.0, -2.0, -3.0])) == 3

    def test_both_no_streaks(self):
        # All zeros → no wins, no losses
        assert H.calc_max_consecutive_wins(pd.Series([0.0, 0.0])) == 0
        assert H.calc_max_consecutive_losses(pd.Series([0.0, 0.0])) == 0


# ────────────────────────────────────────────────────────────────────
# Position-based helpers — duck-typed position stubs
# ────────────────────────────────────────────────────────────────────


class _StubPnl:
    """Stand-in for ``nautilus_trader.model.objects.Money``.

    Supports ``as_double()`` and ``str()`` pathways, matching how the
    helpers unpack PnL values.
    """

    def __init__(self, value: float, use_as_double: bool = True, currency: str = "USDT"):
        self._value = value
        self._use_as_double = use_as_double
        self._currency = currency

    def as_double(self) -> float:
        if not self._use_as_double:
            raise AttributeError("as_double disabled for this stub")
        return self._value

    def __str__(self) -> str:  # pragma: no cover - used by fallback parse
        return f"{self._value} {self._currency}"


class _StubPosition:
    def __init__(self, duration_ns: int | None = None, realized_pnl=None, commissions=None):
        self.duration_ns = duration_ns
        self.realized_pnl = realized_pnl
        self._commissions = commissions or {}

    def commissions(self):
        return dict(self._commissions)


def _pos(duration_ns: int | None = None, pnl: float | None = None, **kw) -> _StubPosition:
    """Factory — returns a position stub with PnL wrapped as a Money-like obj."""
    pnl_obj = _StubPnl(pnl) if pnl is not None else None
    return _StubPosition(duration_ns=duration_ns, realized_pnl=pnl_obj, **kw)


class TestPositionDurations:
    def test_trade_duration_none_input(self):
        assert H.calc_avg_trade_duration(None) is None

    def test_trade_duration_empty(self):
        assert H.calc_avg_trade_duration([]) is None

    def test_trade_duration_all_zero_durations(self):
        # duration_ns=0 is skipped (not positive)
        positions = [_pos(duration_ns=0, pnl=1.0), _pos(duration_ns=None, pnl=1.0)]
        assert H.calc_avg_trade_duration(positions) is None

    def test_trade_duration_happy_path(self):
        # 1h + 3h → avg 2h = 2 * 3600 * 1e9 ns
        one_hour_ns = 3600 * 1_000_000_000
        positions = [
            _pos(duration_ns=one_hour_ns, pnl=1.0),
            _pos(duration_ns=3 * one_hour_ns, pnl=-1.0),
        ]
        result = H.calc_avg_trade_duration(positions)
        assert result == "2h"

    def test_trade_duration_skips_missing_duration(self):
        one_day_ns = 86400 * 1_000_000_000
        positions = [
            _pos(duration_ns=one_day_ns, pnl=1.0),
            _pos(duration_ns=None, pnl=1.0),
            _pos(duration_ns=0, pnl=1.0),
        ]
        result = H.calc_avg_trade_duration(positions)
        # Only the one valid duration counted
        assert result == "1d"

    def test_winning_duration_filters_by_pnl(self):
        five_min_ns = 5 * 60 * 1_000_000_000
        fifteen_min_ns = 15 * 60 * 1_000_000_000
        positions = [
            _pos(duration_ns=five_min_ns, pnl=1.0),
            _pos(duration_ns=fifteen_min_ns, pnl=-1.0),  # filtered out
        ]
        assert H.calc_avg_winning_duration(positions) == "5m"

    def test_winning_duration_no_winners(self):
        positions = [_pos(duration_ns=1_000_000_000, pnl=-1.0)]
        assert H.calc_avg_winning_duration(positions) is None

    def test_losing_duration_includes_zero_pnl(self):
        # Contract: zero PnL counts as a "loser" for duration (<= 0 boundary)
        five_sec_ns = 5 * 1_000_000_000
        ten_sec_ns = 10 * 1_000_000_000
        positions = [
            _pos(duration_ns=five_sec_ns, pnl=0.0),
            _pos(duration_ns=ten_sec_ns, pnl=-1.0),
        ]
        # Avg = 7.5s → rounds down to 7s in the formatter
        assert H.calc_avg_losing_duration(positions) == "7s"

    def test_losing_duration_missing_pnl_skipped(self):
        positions = [_StubPosition(duration_ns=1_000_000_000, realized_pnl=None)]
        assert H.calc_avg_losing_duration(positions) is None

    def test_losing_duration_no_losers(self):
        positions = [_pos(duration_ns=1_000_000_000, pnl=1.0)]
        assert H.calc_avg_losing_duration(positions) is None

    def test_duration_handles_stringified_pnl(self):
        # When as_double fails, parser falls back to str(pnl).split()[0]
        pnl_obj = _StubPnl(1.5, use_as_double=False)
        # Patch the stub to have no as_double attribute
        del pnl_obj._use_as_double
        # Recreate without as_double attribute
        class _StringOnlyPnl:
            def __str__(self):
                return "1.5 USDT"
        positions = [
            _StubPosition(duration_ns=60 * 1_000_000_000, realized_pnl=_StringOnlyPnl()),
        ]
        # Winner (1.5 > 0) → counted
        assert H.calc_avg_winning_duration(positions) == "1m"


# ────────────────────────────────────────────────────────────────────
# calc_total_commission
# ────────────────────────────────────────────────────────────────────


class _StubMoney:
    def __init__(self, value: float, use_as_double: bool = True):
        self._value = value
        self._use_as_double = use_as_double

    def as_double(self) -> float:
        if not self._use_as_double:
            raise AttributeError
        return self._value

    def __str__(self) -> str:
        return f"{self._value} USDT"


class TestCalcTotalCommission:
    def test_none_returns_zero(self):
        assert H.calc_total_commission(None) == 0.0

    def test_empty_returns_zero(self):
        assert H.calc_total_commission([]) == 0.0

    def test_sums_across_positions(self):
        positions = [
            _StubPosition(commissions={"USDT": _StubMoney(1.5)}),
            _StubPosition(commissions={"USDT": _StubMoney(2.25)}),
        ]
        assert H.calc_total_commission(positions) == 3.75

    def test_sums_multi_currency(self):
        positions = [
            _StubPosition(commissions={"USDT": _StubMoney(1.0), "BTC": _StubMoney(0.001)}),
        ]
        # Helper just sums floats regardless of currency (matches legacy behaviour)
        assert H.calc_total_commission(positions) == 1.001

    def test_swallows_per_position_errors(self):
        class _Broken:
            def commissions(self):
                raise RuntimeError("boom")

        positions = [
            _Broken(),
            _StubPosition(commissions={"USDT": _StubMoney(2.5)}),
        ]
        # Broken position silently skipped, others still counted
        assert H.calc_total_commission(positions) == 2.5

    def test_string_fallback_when_as_double_missing(self):
        class _StringMoney:
            def __str__(self):
                return "3.14 USDT"

        positions = [_StubPosition(commissions={"USDT": _StringMoney()})]
        assert H.calc_total_commission(positions) == 3.14

    def test_rounds_to_4dp(self):
        positions = [
            _StubPosition(commissions={"USDT": _StubMoney(1.234567)}),
        ]
        assert H.calc_total_commission(positions) == 1.2346


# ────────────────────────────────────────────────────────────────────
# Order-based helpers
# ────────────────────────────────────────────────────────────────────


class _StubOrder:
    def __init__(self, status):
        self.status = status


class TestOrderHelpers:
    def test_total_orders_none(self):
        assert H.calc_total_orders(None) == 0

    def test_total_orders_empty(self):
        assert H.calc_total_orders([]) == 0

    def test_total_orders_counts(self):
        assert H.calc_total_orders([_StubOrder("a"), _StubOrder("b")]) == 2

    def test_filled_orders_none(self):
        assert H.calc_filled_orders(None, "FILLED") == 0

    def test_filled_orders_empty(self):
        assert H.calc_filled_orders([], "FILLED") == 0

    def test_filled_orders_counts_matches(self):
        orders = [
            _StubOrder("FILLED"),
            _StubOrder("SUBMITTED"),
            _StubOrder("FILLED"),
            _StubOrder("CANCELED"),
        ]
        assert H.calc_filled_orders(orders, "FILLED") == 2

    def test_filled_orders_marker_is_enum_agnostic(self):
        # The marker can be any comparable value (int, Enum, str)
        orders = [_StubOrder(7), _StubOrder(7), _StubOrder(3)]
        assert H.calc_filled_orders(orders, 7) == 2

    def test_filled_orders_missing_status_attr(self):
        # Orders without a .status attribute are skipped (getattr default None)
        class _NoStatus:
            pass

        orders = [_StubOrder("FILLED"), _NoStatus()]
        assert H.calc_filled_orders(orders, "FILLED") == 1


# ────────────────────────────────────────────────────────────────────
# NT-independence proof
# ────────────────────────────────────────────────────────────────────


class TestNoNTDependency:
    """Lock that the helpers never import NautilusTrader at module load.

    Any regression — e.g. somebody adding ``from nautilus_trader.X import Y``
    at the top of the helpers module — would break CI images that ship
    without NT.  We prove independence by importing a *fresh* copy of the
    module inside a ``sys.meta_path`` blocker and asserting nothing NT
    lands in ``sys.modules`` afterward.
    """

    def test_module_loads_without_nt(self):
        class _Blocker:
            def find_spec(self, name, path=None, target=None):
                blocked = (
                    name.startswith("nautilus_trader")
                    or name == "optuna"
                    or name == "redis"
                    or name == "httpx"
                    or name == "sqlalchemy"
                )
                if blocked:
                    raise ImportError(f"blocked: {name}")
                return None

        blocker = _Blocker()
        # Snapshot sys.modules so we can isolate
        saved = {
            k: sys.modules.get(k, KeyError)
            for k in [
                "tinohelm.backtest.custom_statistics_helpers",
                "tinohelm.backtest.result",
                "tinohelm.backtest.result.statistics",
            ]
        }
        for k in list(saved):
            sys.modules.pop(k, None)

        sys.meta_path.insert(0, blocker)
        try:
            import importlib
            mod = importlib.import_module("tinohelm.backtest.custom_statistics_helpers")
            # Helpers module loaded successfully despite the blocker
            assert hasattr(mod, "calc_max_drawdown_pct")
            # And no NT got pulled in as a side effect
            nt_loaded = [k for k in sys.modules if k.startswith("nautilus_trader")]
            assert nt_loaded == []
        finally:
            sys.meta_path.remove(blocker)
            for k, v in saved.items():
                if v is KeyError:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v


# ────────────────────────────────────────────────────────────────────
# Cross-helper parity — bundle imports
# ────────────────────────────────────────────────────────────────────


class TestPublicAPI:
    """Lock the public surface so shim refactors can't silently shrink it."""

    def test_all_exports_callable(self):
        for name in H.__all__:
            fn = getattr(H, name)
            assert callable(fn), f"{name} is not callable"

    def test_all_exports_start_with_calc(self):
        # Every public helper must be a ``calc_*`` — a stable naming contract.
        assert all(n.startswith("calc_") for n in H.__all__)

    def test_17_helpers(self):
        # Exactly 17 ``calc_*`` helpers — one per PortfolioStatistic subclass
        # (MaxDrawdownPct, AnnualReturn, CalmarRatioPy, TotalTrades,
        #  WinningTrades, LosingTrades, GrossProfit, GrossLoss,
        #  AvgWinLossRatio, MaxConsecutiveWins, MaxConsecutiveLosses,
        #  AvgTradeDuration, AvgWinningDuration, AvgLosingDuration,
        #  TotalCommission, TotalOrders, FilledOrders).
        assert len(H.__all__) == 17
