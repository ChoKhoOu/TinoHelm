"""Pure-computation helpers for custom portfolio statistics.

This module extracts the **real math** used by the ``PortfolioStatistic``
subclasses in :mod:`tinohelm.backtest.custom_statistics` so the logic can
be unit-tested without having NautilusTrader installed.

All helpers operate on primitive inputs (``pandas.Series``, ``list`` of
duck-typed position/order objects).  Position inputs only require a
stable set of attributes:

* ``duration_ns`` — integer nanosecond holding time
* ``realized_pnl`` — NT ``Money`` object or anything accepted by
  :func:`_parse_realized_pnl`
* ``commissions()`` — dict-like returning currency → Money mapping

The helpers are deliberately NT-free; pass stub objects or real NT
positions indifferently.  See ``tests/backtest/test_custom_statistics_helpers.py``
for usage examples.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

import pandas as pd

# Import directly from the NT-free leaf module to avoid pulling in
# ``result/extract.py`` which requires ``nautilus_trader``.
from tinohelm.backtest.result.statistics import (
    _format_duration_ns,
    _parse_realized_pnl,
)

__all__ = [
    # Returns-based
    "calc_max_drawdown_pct",
    "calc_annual_return",
    "calc_calmar_ratio",
    # PnL-based
    "calc_total_trades",
    "calc_winning_trades",
    "calc_losing_trades",
    "calc_gross_profit",
    "calc_gross_loss",
    "calc_avg_win_loss_ratio",
    "calc_max_consecutive_wins",
    "calc_max_consecutive_losses",
    # Position-based
    "calc_avg_trade_duration",
    "calc_avg_winning_duration",
    "calc_avg_losing_duration",
    "calc_total_commission",
    # Order-based
    "calc_total_orders",
    "calc_filled_orders",
]

# Rounding precisions — mirror the legacy class behaviour exactly.
_DRAWDOWN_DECIMALS = 6
_CAGR_DECIMALS = 6
_CALMAR_DECIMALS = 4
_PROFIT_DECIMALS = 4
_WIN_LOSS_DECIMALS = 4
_COMMISSION_DECIMALS = 4


# ---------------------------------------------------------------------------
# Returns-based helpers
# ---------------------------------------------------------------------------

def calc_max_drawdown_pct(raw_returns: pd.Series | None) -> float | None:
    """Maximum drawdown as a fraction (e.g. ``-0.15`` for -15%).

    Returns ``None`` for empty/``None`` input or when the resulting
    drawdown minimum is NaN.
    """
    if raw_returns is None or raw_returns.empty:
        return None
    cum = (1 + raw_returns).cumprod()
    peak = cum.cummax()
    dd = (cum - peak) / peak
    val = dd.min()
    return round(float(val), _DRAWDOWN_DECIMALS) if pd.notna(val) else None


def calc_annual_return(raw_returns: pd.Series | None) -> float | None:
    """CAGR annualised to 252 trading days.

    Returns ``None`` when the series has fewer than 2 observations or
    the cumulative return is non-positive (undefined CAGR).
    """
    if raw_returns is None or len(raw_returns) < 2:
        return None
    cum = (1 + raw_returns).cumprod()
    total_ret = float(cum.iloc[-1])
    n_days = len(raw_returns)
    if total_ret <= 0 or n_days == 0:
        return None
    cagr = total_ret ** (252.0 / n_days) - 1.0
    return round(cagr, _CAGR_DECIMALS)


def calc_calmar_ratio(raw_returns: pd.Series | None) -> float | None:
    """CAGR divided by the absolute max drawdown (annualised to 252 days).

    Returns ``None`` when CAGR is undefined or drawdown is zero.
    """
    if raw_returns is None or len(raw_returns) < 2:
        return None
    cum = (1 + raw_returns).cumprod()
    total_ret = float(cum.iloc[-1])
    n_days = len(raw_returns)
    if total_ret <= 0 or n_days == 0:
        return None
    cagr = total_ret ** (252.0 / n_days) - 1.0
    peak = cum.cummax()
    dd = (cum - peak) / peak
    max_dd = abs(float(dd.min()))
    if max_dd == 0:
        return None
    return round(cagr / max_dd, _CALMAR_DECIMALS)


# ---------------------------------------------------------------------------
# PnL-based helpers
# ---------------------------------------------------------------------------

def calc_total_trades(realized_pnls: pd.Series | None) -> int:
    """Number of completed round-trip trades.  ``None`` → ``0``."""
    if realized_pnls is None:
        return 0
    return len(realized_pnls)


def calc_winning_trades(realized_pnls: pd.Series | None) -> int:
    """Number of trades with strictly positive PnL."""
    if realized_pnls is None or realized_pnls.empty:
        return 0
    return int((realized_pnls > 0).sum())


def calc_losing_trades(realized_pnls: pd.Series | None) -> int:
    """Number of trades with strictly negative PnL.

    Note: trades with exactly zero PnL are **not** counted as losers.
    This matches the historical class semantics; :func:`calc_gross_loss`
    uses a different boundary (``<= 0``) on purpose.
    """
    if realized_pnls is None or realized_pnls.empty:
        return 0
    return int((realized_pnls < 0).sum())


def calc_gross_profit(realized_pnls: pd.Series | None) -> float:
    """Sum of all winning trade PnLs.  ``None``/empty → ``0.0``."""
    if realized_pnls is None or realized_pnls.empty:
        return 0.0
    winners = realized_pnls[realized_pnls > 0]
    return round(float(winners.sum()), _PROFIT_DECIMALS)


def calc_gross_loss(realized_pnls: pd.Series | None) -> float:
    """Sum of all losing trade PnLs (returned as negative or zero).

    Unlike :func:`calc_losing_trades`, break-even trades (PnL == 0) are
    included in the loss bucket because summing them is a no-op — the
    resulting figure is identical either way, but the historical
    implementation used ``<= 0``.  We preserve that exactly.
    """
    if realized_pnls is None or realized_pnls.empty:
        return 0.0
    losers = realized_pnls[realized_pnls <= 0]
    return round(float(losers.sum()), _PROFIT_DECIMALS)


def calc_avg_win_loss_ratio(realized_pnls: pd.Series | None) -> float | None:
    """Average winning PnL divided by the absolute average losing PnL.

    Returns ``None`` when there are no winners, no losers, or the
    average loss is exactly zero.
    """
    if realized_pnls is None or realized_pnls.empty:
        return None
    winners = realized_pnls[realized_pnls > 0]
    losers = realized_pnls[realized_pnls < 0]
    if winners.empty or losers.empty:
        return None
    avg_win = winners.mean()
    avg_loss = abs(losers.mean())
    if avg_loss == 0:
        return None
    return round(float(avg_win / avg_loss), _WIN_LOSS_DECIMALS)


def _max_consecutive(values: Iterable[float], predicate: Callable[[float], bool]) -> int:
    """Longest run of elements for which *predicate* returns True.

    Any element that doesn't satisfy the predicate resets the streak
    counter, regardless of sign — this mirrors the legacy branch
    structure where win-streaks are reset by *any* non-positive PnL
    (including zero).
    """
    max_streak = 0
    current = 0
    for v in values:
        if predicate(v):
            current += 1
            if current > max_streak:
                max_streak = current
        else:
            current = 0
    return max_streak


def calc_max_consecutive_wins(realized_pnls: pd.Series | None) -> int:
    """Longest winning streak (strictly positive PnLs in a row)."""
    if realized_pnls is None or realized_pnls.empty:
        return 0
    return _max_consecutive(realized_pnls, lambda v: v > 0)


def calc_max_consecutive_losses(realized_pnls: pd.Series | None) -> int:
    """Longest losing streak (strictly negative PnLs in a row)."""
    if realized_pnls is None or realized_pnls.empty:
        return 0
    return _max_consecutive(realized_pnls, lambda v: v < 0)


# ---------------------------------------------------------------------------
# Position-based helpers
# ---------------------------------------------------------------------------

def _positive_durations(positions: Iterable) -> list[int]:
    """Collect strictly-positive ``duration_ns`` values from positions."""
    out: list[int] = []
    for p in positions:
        dur = getattr(p, "duration_ns", None)
        if dur and dur > 0:
            out.append(int(dur))
    return out


def _filtered_durations(
    positions: Iterable,
    predicate: Callable[[float], bool],
) -> list[int]:
    """Collect ``duration_ns`` values for positions matching a PnL predicate.

    Positions missing ``realized_pnl`` or with non-positive duration are
    skipped.
    """
    out: list[int] = []
    for p in positions:
        pnl = getattr(p, "realized_pnl", None)
        dur = getattr(p, "duration_ns", None)
        if pnl is None or not dur or dur <= 0:
            continue
        pnl_val = _parse_realized_pnl(pnl)
        if predicate(pnl_val):
            out.append(int(dur))
    return out


def calc_avg_trade_duration(positions: Iterable | None) -> str | None:
    """Average holding time as a human-readable string, or ``None``."""
    if not positions:
        return None
    durations = _positive_durations(positions)
    if not durations:
        return None
    avg_ns = sum(durations) / len(durations)
    return _format_duration_ns(avg_ns)


def calc_avg_winning_duration(positions: Iterable | None) -> str | None:
    """Average holding time for winners (PnL > 0), or ``None``."""
    if not positions:
        return None
    durations = _filtered_durations(positions, lambda v: v > 0)
    if not durations:
        return None
    return _format_duration_ns(sum(durations) / len(durations))


def calc_avg_losing_duration(positions: Iterable | None) -> str | None:
    """Average holding time for losers (PnL <= 0), or ``None``.

    Includes break-even trades to mirror the legacy class semantics —
    this matches :func:`calc_gross_loss` rather than
    :func:`calc_losing_trades`.
    """
    if not positions:
        return None
    durations = _filtered_durations(positions, lambda v: v <= 0)
    if not durations:
        return None
    return _format_duration_ns(sum(durations) / len(durations))


def calc_total_commission(positions: Iterable | None) -> float:
    """Total commission (fees) across all positions.

    Silently swallows per-position errors — mirrors the historical
    contract: if any one commission object fails to unpack, skip it
    rather than crash the whole tearsheet render.
    """
    if not positions:
        return 0.0
    total = 0.0
    for p in positions:
        try:
            for money in p.commissions().values():
                if hasattr(money, "as_double"):
                    total += float(money.as_double())
                else:
                    total += float(str(money).split()[0])
        except Exception:
            continue
    return round(total, _COMMISSION_DECIMALS)


# ---------------------------------------------------------------------------
# Order-based helpers
# ---------------------------------------------------------------------------

def calc_total_orders(orders: list | None) -> int:
    """Number of submitted orders.  ``None``/empty → ``0``."""
    return len(orders) if orders else 0


def calc_filled_orders(orders: list | None, filled_status: Any) -> int:
    """Number of orders with ``status == filled_status``.

    *filled_status* is injected so this helper stays NT-free; the caller
    (``FilledOrders`` statistic) supplies ``OrderStatus.FILLED`` from NT.
    Callers that test this helper can supply any marker value.
    """
    if not orders:
        return 0
    return sum(1 for o in orders if getattr(o, "status", None) == filled_status)
