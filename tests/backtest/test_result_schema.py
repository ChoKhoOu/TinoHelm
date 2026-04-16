"""Schema-locking integration test for ``extract_backtest_results``.

Purpose: after extracting Section 12b/14 into pure helpers, the *aggregation*
layer in ``extract.py`` is now essentially an orchestrator.  This test pins
the full set of keys the aggregator emits so that accidental renames or
removals break the suite immediately — the frontend depends on this contract.

It uses a MagicMock-based engine deliberately kept light: most section-level
calculations return empty lists/dicts under a minimal engine, which is
exactly what we need to assert the *schema* (keys) rather than values.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Expected schemas (the contract with the frontend / artifacts writer).
# Any change here must be accompanied by a conscious decision — the
# frontend's `BacktestResult` type should also be updated.
# ---------------------------------------------------------------------------

TOP_LEVEL_KEYS = frozenset({
    "statistics",
    "equity_curve",
    "trade_log",
    "per_instrument",
    "monthly_returns",
    "weekly_returns",
    "drawdown_periods",
    "slippage_stats",
    "instrument_cumulative_pnl",
    "instrument_correlation",
    "monthly_pnl_heatmap",
    "portfolio_analytics",
    "annual_returns",
    "rolling_returns",
    "returns_distribution",
    "qq_plot_data",
    "benchmark_equity_curve",
    "daily_returns",
    "rolling_sharpe",
    "rolling_sortino",
    "rolling_volatility",
    "rolling_beta",
    "benchmark_type",
    "trade_pnl_distribution",
    "cumulative_trade_pnl",
    "trade_pnl_scatter",
    "mae_mfe",
    "holding_time_distribution",
    "streak_sequence",
    "long_vs_short",
    "return_by_dow",
    "return_by_hour",
    "robustness",
})

STATISTICS_KEYS = frozenset({
    # Core PnL / returns
    "total_pnl", "total_return_pct", "annual_return",
    "sharpe_ratio", "sortino_ratio", "calmar_ratio",
    "max_drawdown", "returns_volatility",
    # Win/loss counts
    "win_rate", "profit_factor", "expectancy",
    "total_trades", "winning_trades", "losing_trades",
    "largest_win", "largest_loss", "avg_win", "avg_loss",
    "avg_win_loss_ratio", "winning_streak", "losing_streak",
    "long_pct", "short_pct",
    # Holding-time
    "avg_holding_time", "avg_winning_holding_time", "avg_losing_holding_time",
    # Fees & orders
    "total_fees", "gross_profit", "gross_loss",
    "open_positions", "total_orders", "filled_orders", "final_balance",
    # Extended stats (section 8c)
    "best_day", "worst_day", "best_month", "worst_month",
    "positive_days_pct", "skewness", "kurtosis", "tail_ratio", "stability",
    # Extended risk
    "omega_ratio", "var_95", "var_99", "cvar_95", "downside_deviation",
    "ulcer_index", "max_daily_loss", "positive_months_pct",
    "normal_dist_mean", "normal_dist_std",
    # Benchmark-relative (section 11k)
    "alpha", "beta", "r_squared", "information_ratio",
    # Trade scalar metrics (section 12b)
    "median_trade_pnl", "std_trade_pnl", "fill_rate", "avg_trades_per_day",
    "recovery_factor", "sqn", "kelly_criterion", "k_ratio", "expectancy_r",
})

ROBUSTNESS_REQUIRED_KEYS = frozenset({
    "psr", "min_backtest_length_days",
    "actual_backtest_length_days", "backtest_length_sufficient",
})

# Present only when ≥ 2 trades so Monte Carlo can run.
ROBUSTNESS_MC_KEYS = frozenset({
    "mc_equity_cone",
    "mc_probability_of_loss",
    "mc_5th_percentile_return",
    "mc_median_max_drawdown",
    "mc_median_final_return",
    "mc_num_simulations",
})


# ---------------------------------------------------------------------------
# Minimal mock engine — exercises all code paths but without rich data, so
# most sections return empty lists/dicts (which is perfect for schema checks).
# ---------------------------------------------------------------------------

def _mock_position(instrument: str, pnl: float, *, side: str = "BUY",
                   ts_opened: int = 1_700_000_000_000_000_000,
                   ts_closed: int = 1_700_000_100_000_000_000):
    pos = MagicMock()
    pos.instrument_id.__str__ = MagicMock(return_value=instrument)
    pos.is_closed = True
    pos.is_open = False
    pos.entry = MagicMock()
    pos.entry.name = side
    pos.peak_qty.__str__ = MagicMock(return_value="1.0")
    pos.avg_px_open = 50000.0
    pos.avg_px_close = 50000.0 + pnl
    pos.realized_pnl = MagicMock()
    pos.realized_pnl.as_double = MagicMock(return_value=pnl)
    pos.realized_pnl.__str__ = MagicMock(return_value=f"{pnl} USDT")
    pos.ts_opened = ts_opened
    pos.ts_closed = ts_closed
    pos.duration_ns = ts_closed - ts_opened
    return pos


def _mock_engine(positions: list):
    engine = MagicMock()
    engine.cache.positions.return_value = positions
    engine.cache.orders.return_value = []
    engine.cache.bar_types.return_value = []
    engine.cache.bars.return_value = []
    engine.cache.instrument_ids.return_value = []
    engine.cache.accounts.return_value = []

    ret = pd.Series(
        [0.01, -0.005, 0.02, 0.015, -0.01],
        index=pd.date_range("2024-01-01", periods=5, freq="D"),
    )
    engine.portfolio.analyzer.returns.return_value = ret
    engine.portfolio.analyzer.get_performance_stats_pnls.return_value = {}
    engine.portfolio.analyzer.get_performance_stats_returns.return_value = {}
    engine.portfolio.analyzer.get_performance_stats_general.return_value = {}

    empty_df = MagicMock()
    empty_df.empty = True
    engine.trader.generate_fills_report.return_value = empty_df
    engine.trader.generate_positions_report.return_value = empty_df
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResultSchema:
    """The aggregator must produce a stable key schema regardless of data."""

    def test_top_level_keys_exact_match(self):
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([
            _mock_position("BTCUSDT-PERP.BINANCE", 100.0),
            _mock_position("BTCUSDT-PERP.BINANCE", -30.0),
            _mock_position("ETHUSDT-PERP.BINANCE", 50.0),
        ])

        result = extract_backtest_results(engine, starting_balance=10000)
        assert frozenset(result.keys()) == TOP_LEVEL_KEYS

    def test_statistics_keys_exact_match(self):
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([
            _mock_position("BTCUSDT-PERP.BINANCE", 100.0),
            _mock_position("BTCUSDT-PERP.BINANCE", -30.0),
        ])

        result = extract_backtest_results(engine, starting_balance=10000)
        assert frozenset(result["statistics"].keys()) == STATISTICS_KEYS

    def test_robustness_core_keys_always_present(self):
        """Even with only 1 trade (MC skipped), PSR/MBL block must be present."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([_mock_position("BTCUSDT-PERP.BINANCE", 100.0)])
        result = extract_backtest_results(engine, starting_balance=10000)

        assert result["robustness"] is not None
        assert ROBUSTNESS_REQUIRED_KEYS.issubset(result["robustness"].keys())

    def test_robustness_mc_keys_present_with_sufficient_trades(self):
        """≥2 trades → MC simulation runs → extra keys appear."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([
            _mock_position("BTCUSDT-PERP.BINANCE", 100.0),
            _mock_position("BTCUSDT-PERP.BINANCE", -30.0),
            _mock_position("BTCUSDT-PERP.BINANCE", 50.0),
            _mock_position("BTCUSDT-PERP.BINANCE", -10.0),
        ])
        result = extract_backtest_results(engine, starting_balance=10000)

        assert result["robustness"] is not None
        assert ROBUSTNESS_MC_KEYS.issubset(result["robustness"].keys())

    def test_robustness_skipped_when_flag_disabled(self):
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([_mock_position("BTCUSDT-PERP.BINANCE", 10.0)])
        result = extract_backtest_results(
            engine, starting_balance=10000, compute_robustness=False,
        )
        assert result["robustness"] is None

    def test_container_types_are_expected_shapes(self):
        """Top-level values must be the shapes the frontend consumes."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([
            _mock_position("BTCUSDT-PERP.BINANCE", 100.0),
            _mock_position("BTCUSDT-PERP.BINANCE", -30.0),
        ])
        result = extract_backtest_results(engine, starting_balance=10000)

        # Dicts
        for key in ("statistics", "per_instrument", "slippage_stats",
                    "instrument_cumulative_pnl", "instrument_correlation",
                    "portfolio_analytics", "long_vs_short"):
            assert isinstance(result[key], dict), f"{key} must be dict"

        # Lists
        for key in ("equity_curve", "trade_log", "monthly_returns",
                    "weekly_returns", "drawdown_periods", "annual_returns",
                    "rolling_returns", "returns_distribution", "qq_plot_data",
                    "benchmark_equity_curve", "daily_returns", "rolling_sharpe",
                    "rolling_sortino", "rolling_volatility", "rolling_beta",
                    "trade_pnl_distribution", "cumulative_trade_pnl",
                    "trade_pnl_scatter", "mae_mfe", "holding_time_distribution",
                    "streak_sequence", "return_by_dow", "return_by_hour",
                    "monthly_pnl_heatmap"):
            assert isinstance(result[key], list), f"{key} must be list"

        # Strings
        assert isinstance(result["benchmark_type"], str)

    def test_empty_positions_still_returns_complete_schema(self):
        """Zero trades → every schema key present with sensible empty defaults."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([])
        result = extract_backtest_results(engine, starting_balance=10000)

        assert frozenset(result.keys()) == TOP_LEVEL_KEYS
        assert frozenset(result["statistics"].keys()) == STATISTICS_KEYS
        assert result["statistics"]["total_trades"] == 0
        # Scalar metrics should be None when no trades, not missing
        for scalar in ("median_trade_pnl", "std_trade_pnl", "fill_rate",
                       "avg_trades_per_day", "recovery_factor", "sqn",
                       "kelly_criterion", "k_ratio", "expectancy_r"):
            assert scalar in result["statistics"]

    def test_trade_scalar_metrics_populated_when_trades_present(self):
        """Confirm scalar metrics are NOT left at None when trades exist."""
        from tinohelm.backtest.result import extract_backtest_results

        engine = _mock_engine([
            _mock_position("BTCUSDT-PERP.BINANCE", 100.0),
            _mock_position("BTCUSDT-PERP.BINANCE", -30.0),
            _mock_position("BTCUSDT-PERP.BINANCE", 50.0),
            _mock_position("BTCUSDT-PERP.BINANCE", -10.0),
        ])
        result = extract_backtest_results(engine, starting_balance=10000)

        stats = result["statistics"]
        # With 4 trades, at least these must resolve to real numbers
        assert stats["median_trade_pnl"] is not None
        assert stats["std_trade_pnl"] is not None
        assert stats["avg_trades_per_day"] is not None
        assert stats["sqn"] is not None
