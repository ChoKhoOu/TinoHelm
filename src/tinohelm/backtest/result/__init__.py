"""Backtest result extraction package.

Re-exports for backward compatibility.
"""
from tinohelm.backtest.result.extract import extract_backtest_results
from tinohelm.backtest.result.statistics import (
    _compute_monte_carlo,
    _compute_psr,
    _compute_min_backtest_length,
    _compute_streaks,
    _format_duration_ns,
    _format_ns_timestamp,
    _format_order_side,
    _norm_cdf,
    _norm_ppf,
    _parse_realized_pnl,
    _safe_float,
)

__all__ = [
    "extract_backtest_results",
    "_safe_float",
    "_format_duration_ns",
    "_parse_realized_pnl",
    "_format_order_side",
    "_format_ns_timestamp",
    "_compute_streaks",
    "_norm_ppf",
    "_norm_cdf",
    "_compute_psr",
    "_compute_min_backtest_length",
    "_compute_monte_carlo",
]
