"""Custom PortfolioStatistic implementations for richer tearsheet reports.

These supplement NT's built-in Rust statistics with trade-level metrics
that are missing from the default tearsheet (total trades, streaks,
holding times, fees, etc.).

Register them with ``engine.portfolio.analyzer.register_statistic(stat)``
before ``engine.run()`` so they appear in the stats_table chart.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from nautilus_trader.analysis.statistic import PortfolioStatistic

from tinohelm.backtest.result import _format_duration_ns


# ---------------------------------------------------------------------------
# Returns-based statistics (fallbacks for Rust versions that return None)
# ---------------------------------------------------------------------------

class MaxDrawdownPct(PortfolioStatistic):
    """Maximum drawdown as a percentage (e.g. -0.15 = -15%)."""

    @property
    def name(self) -> str:
        return "Max Drawdown"

    def calculate_from_returns(self, raw_returns: pd.Series) -> Any | None:
        if raw_returns is None or raw_returns.empty:
            return None
        cum = (1 + raw_returns).cumprod()
        peak = cum.cummax()
        dd = (cum - peak) / peak
        val = dd.min()
        return round(float(val), 6) if pd.notna(val) else None


class AnnualReturn(PortfolioStatistic):
    """Compound Annual Growth Rate (CAGR), annualized to 252 trading days."""

    @property
    def name(self) -> str:
        return "CAGR (252 days)"

    def calculate_from_returns(self, raw_returns: pd.Series) -> Any | None:
        if raw_returns is None or len(raw_returns) < 2:
            return None
        cum = (1 + raw_returns).cumprod()
        total_ret = float(cum.iloc[-1])
        n_days = len(raw_returns)
        if total_ret <= 0 or n_days == 0:
            return None
        cagr = total_ret ** (252.0 / n_days) - 1.0
        return round(cagr, 6)


class CalmarRatioPy(PortfolioStatistic):
    """Calmar Ratio = CAGR / |Max Drawdown|, annualized to 252 days."""

    @property
    def name(self) -> str:
        return "Calmar Ratio (252 days)"

    def calculate_from_returns(self, raw_returns: pd.Series) -> Any | None:
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
        return round(cagr / max_dd, 4)


# ---------------------------------------------------------------------------
# PnL-based statistics
# ---------------------------------------------------------------------------

class TotalTrades(PortfolioStatistic):
    """Total number of completed (round-trip) trades."""

    @property
    def name(self) -> str:
        return "Total Trades"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None:
            return 0
        return len(realized_pnls)


class WinningTrades(PortfolioStatistic):
    """Number of profitable trades."""

    @property
    def name(self) -> str:
        return "Winning Trades"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0
        return int((realized_pnls > 0).sum())


class LosingTrades(PortfolioStatistic):
    """Number of unprofitable trades."""

    @property
    def name(self) -> str:
        return "Losing Trades"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0
        return int((realized_pnls < 0).sum())


class GrossProfit(PortfolioStatistic):
    """Sum of all winning trade PnLs."""

    @property
    def name(self) -> str:
        return "Gross Profit"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        winners = realized_pnls[realized_pnls > 0]
        return round(float(winners.sum()), 4)


class GrossLoss(PortfolioStatistic):
    """Sum of all losing trade PnLs (reported as negative)."""

    @property
    def name(self) -> str:
        return "Gross Loss"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0.0
        losers = realized_pnls[realized_pnls <= 0]
        return round(float(losers.sum()), 4)


class AvgWinLossRatio(PortfolioStatistic):
    """Ratio of average win to average loss magnitude."""

    @property
    def name(self) -> str:
        return "Avg Win/Loss Ratio"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
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
        return round(float(avg_win / avg_loss), 4)


class MaxConsecutiveWins(PortfolioStatistic):
    """Longest winning streak."""

    @property
    def name(self) -> str:
        return "Max Consecutive Wins"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0
        max_streak = 0
        current = 0
        for pnl in realized_pnls:
            if pnl > 0:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak


class MaxConsecutiveLosses(PortfolioStatistic):
    """Longest losing streak."""

    @property
    def name(self) -> str:
        return "Max Consecutive Losses"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        if realized_pnls is None or realized_pnls.empty:
            return 0
        max_streak = 0
        current = 0
        for pnl in realized_pnls:
            if pnl < 0:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak


# ---------------------------------------------------------------------------
# Position-based statistics
# ---------------------------------------------------------------------------

class AvgTradeDuration(PortfolioStatistic):
    """Average trade holding time as a human-readable string."""

    @property
    def name(self) -> str:
        return "Avg Trade Duration"

    def calculate_from_positions(self, positions: list) -> Any | None:
        if not positions:
            return None
        durations = []
        for p in positions:
            dur = getattr(p, "duration_ns", None)
            if dur and dur > 0:
                durations.append(int(dur))
        if not durations:
            return None
        avg_ns = sum(durations) / len(durations)
        return _format_duration_ns(avg_ns)


class AvgWinningDuration(PortfolioStatistic):
    """Average holding time for winning trades."""

    @property
    def name(self) -> str:
        return "Avg Winning Duration"

    def calculate_from_positions(self, positions: list) -> Any | None:
        if not positions:
            return None
        durations = []
        for p in positions:
            pnl = getattr(p, "realized_pnl", None)
            dur = getattr(p, "duration_ns", None)
            if pnl is not None and dur and dur > 0:
                pnl_val = float(pnl.as_double()) if hasattr(pnl, "as_double") else float(str(pnl).split()[0])
                if pnl_val > 0:
                    durations.append(int(dur))
        if not durations:
            return None
        return _format_duration_ns(sum(durations) / len(durations))


class AvgLosingDuration(PortfolioStatistic):
    """Average holding time for losing trades."""

    @property
    def name(self) -> str:
        return "Avg Losing Duration"

    def calculate_from_positions(self, positions: list) -> Any | None:
        if not positions:
            return None
        durations = []
        for p in positions:
            pnl = getattr(p, "realized_pnl", None)
            dur = getattr(p, "duration_ns", None)
            if pnl is not None and dur and dur > 0:
                pnl_val = float(pnl.as_double()) if hasattr(pnl, "as_double") else float(str(pnl).split()[0])
                if pnl_val <= 0:
                    durations.append(int(dur))
        if not durations:
            return None
        return _format_duration_ns(sum(durations) / len(durations))


# ---------------------------------------------------------------------------
# Order-based statistics
# ---------------------------------------------------------------------------

class TotalOrders(PortfolioStatistic):
    """Total number of orders submitted."""

    @property
    def name(self) -> str:
        return "Total Orders"

    def calculate_from_orders(self, orders: list) -> Any | None:
        return len(orders) if orders else 0


class FilledOrders(PortfolioStatistic):
    """Number of orders that were completely filled."""

    @property
    def name(self) -> str:
        return "Filled Orders"

    def calculate_from_orders(self, orders: list) -> Any | None:
        if not orders:
            return 0
        from nautilus_trader.model.enums import OrderStatus
        return sum(1 for o in orders if o.status == OrderStatus.FILLED)


class TotalCommission(PortfolioStatistic):
    """Total commission/fees paid across all positions."""

    @property
    def name(self) -> str:
        return "Total Commission"

    def calculate_from_positions(self, positions: list) -> Any | None:
        if not positions:
            return 0.0
        total = 0.0
        for p in positions:
            try:
                for money in p.commissions().values():
                    total += float(money.as_double()) if hasattr(money, "as_double") else float(str(money).split()[0])
            except Exception:
                pass
        return round(total, 4)


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

ALL_CUSTOM_STATISTICS: list[type[PortfolioStatistic]] = [
    # Returns-based
    MaxDrawdownPct,
    AnnualReturn,
    CalmarRatioPy,
    # PnL-based
    TotalTrades,
    WinningTrades,
    LosingTrades,
    GrossProfit,
    GrossLoss,
    AvgWinLossRatio,
    MaxConsecutiveWins,
    MaxConsecutiveLosses,
    # Position-based
    AvgTradeDuration,
    AvgWinningDuration,
    AvgLosingDuration,
    TotalCommission,
    # Order-based
    TotalOrders,
    FilledOrders,
]


def register_custom_statistics(analyzer) -> int:
    """Register all custom statistics with the given PortfolioAnalyzer.

    Deregisters any conflicting built-in stats with the same name first
    (e.g. Rust CAGR/Calmar/MaxDrawdown that return None).

    Returns the number of statistics registered.
    """
    registered = 0
    for stat_cls in ALL_CUSTOM_STATISTICS:
        try:
            stat = stat_cls()
            # Deregister any existing stat with same name to avoid duplicates
            try:
                analyzer.deregister_statistic(stat)
            except Exception:
                pass
            analyzer.register_statistic(stat)
            registered += 1
        except Exception:
            pass
    return registered
