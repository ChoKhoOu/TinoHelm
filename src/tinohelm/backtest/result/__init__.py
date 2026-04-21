"""Backtest result extraction package.

Re-exports for backward compatibility.

``extract_backtest_results`` is imported lazily on attribute access because
its module (:mod:`tinohelm.backtest.result.extract`) depends on
NautilusTrader.  Keeping the top-level package import NT-free lets
pure-Python helpers such as :mod:`tinohelm.backtest.result.statistics`
and :mod:`tinohelm.backtest.custom_statistics_helpers` be unit-tested
in lean CI environments without the NT wheel.
"""
from __future__ import annotations

from typing import Any

from tinohelm.backtest.result.statistics import (
    _compute_min_backtest_length,
    _compute_monte_carlo,
    _compute_psr,
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


def __getattr__(name: str) -> Any:
    """Lazy re-export of NT-dependent symbols (PEP 562)."""
    if name == "extract_backtest_results":
        from tinohelm.backtest.result.extract import extract_backtest_results
        return extract_backtest_results
    raise AttributeError(f"module 'tinohelm.backtest.result' has no attribute {name!r}")
