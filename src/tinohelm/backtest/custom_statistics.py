"""Custom PortfolioStatistic implementations for richer tearsheet reports.

These supplement NT's built-in Rust statistics with trade-level metrics
that are missing from the default tearsheet (total trades, streaks,
holding times, fees, etc.).

All the actual math lives in :mod:`tinohelm.backtest.custom_statistics_helpers`
— an NT-free module that can be unit-tested without having NautilusTrader
installed.  The classes below are thin ``PortfolioStatistic`` wrappers
that NT's analyzer can discover and invoke.

Register them with ``engine.portfolio.analyzer.register_statistic(stat)``
before ``engine.run()`` so they appear in the stats_table chart.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
from nautilus_trader.analysis.statistic import PortfolioStatistic

from tinohelm.backtest.custom_statistics_helpers import (
    calc_annual_return,
    calc_avg_losing_duration,
    calc_avg_trade_duration,
    calc_avg_win_loss_ratio,
    calc_avg_winning_duration,
    calc_calmar_ratio,
    calc_filled_orders,
    calc_gross_loss,
    calc_gross_profit,
    calc_losing_trades,
    calc_max_consecutive_losses,
    calc_max_consecutive_wins,
    calc_max_drawdown_pct,
    calc_total_commission,
    calc_total_orders,
    calc_total_trades,
    calc_winning_trades,
)


# ---------------------------------------------------------------------------
# Returns-based statistics (fallbacks for Rust versions that return None)
# ---------------------------------------------------------------------------

class MaxDrawdownPct(PortfolioStatistic):
    """Maximum drawdown as a percentage (e.g. -0.15 = -15%)."""

    @property
    def name(self) -> str:
        return "Max Drawdown"

    def calculate_from_returns(self, raw_returns: pd.Series) -> Any | None:
        return calc_max_drawdown_pct(raw_returns)


class AnnualReturn(PortfolioStatistic):
    """Compound Annual Growth Rate (CAGR), annualized to 252 trading days."""

    @property
    def name(self) -> str:
        return "CAGR (252 days)"

    def calculate_from_returns(self, raw_returns: pd.Series) -> Any | None:
        return calc_annual_return(raw_returns)


class CalmarRatioPy(PortfolioStatistic):
    """Calmar Ratio = CAGR / |Max Drawdown|, annualized to 252 days."""

    @property
    def name(self) -> str:
        return "Calmar Ratio (252 days)"

    def calculate_from_returns(self, raw_returns: pd.Series) -> Any | None:
        return calc_calmar_ratio(raw_returns)


# ---------------------------------------------------------------------------
# PnL-based statistics
# ---------------------------------------------------------------------------

class TotalTrades(PortfolioStatistic):
    """Total number of completed (round-trip) trades."""

    @property
    def name(self) -> str:
        return "Total Trades"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_total_trades(realized_pnls)


class WinningTrades(PortfolioStatistic):
    """Number of profitable trades."""

    @property
    def name(self) -> str:
        return "Winning Trades"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_winning_trades(realized_pnls)


class LosingTrades(PortfolioStatistic):
    """Number of unprofitable trades."""

    @property
    def name(self) -> str:
        return "Losing Trades"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_losing_trades(realized_pnls)


class GrossProfit(PortfolioStatistic):
    """Sum of all winning trade PnLs."""

    @property
    def name(self) -> str:
        return "Gross Profit"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_gross_profit(realized_pnls)


class GrossLoss(PortfolioStatistic):
    """Sum of all losing trade PnLs (reported as negative)."""

    @property
    def name(self) -> str:
        return "Gross Loss"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_gross_loss(realized_pnls)


class AvgWinLossRatio(PortfolioStatistic):
    """Ratio of average win to average loss magnitude."""

    @property
    def name(self) -> str:
        return "Avg Win/Loss Ratio"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_avg_win_loss_ratio(realized_pnls)


class MaxConsecutiveWins(PortfolioStatistic):
    """Longest winning streak."""

    @property
    def name(self) -> str:
        return "Max Consecutive Wins"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_max_consecutive_wins(realized_pnls)


class MaxConsecutiveLosses(PortfolioStatistic):
    """Longest losing streak."""

    @property
    def name(self) -> str:
        return "Max Consecutive Losses"

    def calculate_from_realized_pnls(self, realized_pnls: pd.Series) -> Any | None:
        return calc_max_consecutive_losses(realized_pnls)


# ---------------------------------------------------------------------------
# Position-based statistics
# ---------------------------------------------------------------------------

class AvgTradeDuration(PortfolioStatistic):
    """Average trade holding time as a human-readable string."""

    @property
    def name(self) -> str:
        return "Avg Trade Duration"

    def calculate_from_positions(self, positions: list) -> Any | None:
        return calc_avg_trade_duration(positions)


class AvgWinningDuration(PortfolioStatistic):
    """Average holding time for winning trades."""

    @property
    def name(self) -> str:
        return "Avg Winning Duration"

    def calculate_from_positions(self, positions: list) -> Any | None:
        return calc_avg_winning_duration(positions)


class AvgLosingDuration(PortfolioStatistic):
    """Average holding time for losing trades."""

    @property
    def name(self) -> str:
        return "Avg Losing Duration"

    def calculate_from_positions(self, positions: list) -> Any | None:
        return calc_avg_losing_duration(positions)


# ---------------------------------------------------------------------------
# Order-based statistics
# ---------------------------------------------------------------------------

class TotalOrders(PortfolioStatistic):
    """Total number of orders submitted."""

    @property
    def name(self) -> str:
        return "Total Orders"

    def calculate_from_orders(self, orders: list) -> Any | None:
        return calc_total_orders(orders)


class FilledOrders(PortfolioStatistic):
    """Number of orders that were completely filled."""

    @property
    def name(self) -> str:
        return "Filled Orders"

    def calculate_from_orders(self, orders: list) -> Any | None:
        # Import lazily so the module can still be imported by NT-free
        # tooling that doesn't exercise this path.
        from nautilus_trader.model.enums import OrderStatus

        return calc_filled_orders(orders, OrderStatus.FILLED)


class TotalCommission(PortfolioStatistic):
    """Total commission/fees paid across all positions."""

    @property
    def name(self) -> str:
        return "Total Commission"

    def calculate_from_positions(self, positions: list) -> Any | None:
        return calc_total_commission(positions)


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
