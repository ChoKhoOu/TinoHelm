"""Tests for pure helpers in tinohelm.api.routes.backtest.

These cover the estimate/label arithmetic that powers POST /api/backtest/estimate
and the module-surface of shared UUID / progress helpers that the route imports
from tinohelm.api._utils.
"""
from __future__ import annotations

import asyncio

import pytest

from tinohelm.api.routes.backtest import (
    BacktestEstimateRequest,
    estimate_backtest,
    _ARTIFACT_WHITELIST,
    _BARS_PER_DAY_KNOWN,
    _BARS_PER_SEC,
    _calc_bars_per_day,
    _format_estimated_label,
    _INTERVAL_RE,
)


# ---------------------------------------------------------------------------
# Module-surface constants — lock to avoid silent regression
# ---------------------------------------------------------------------------


class TestConstants:
    def test_bars_per_sec_constant(self):
        assert _BARS_PER_SEC == 50_000

    def test_known_intervals(self):
        assert _BARS_PER_DAY_KNOWN == {
            "1m": 1440,
            "5m": 288,
            "15m": 96,
            "1h": 24,
            "4h": 6,
        }

    def test_interval_regex_pattern(self):
        assert _INTERVAL_RE.match("5m")
        assert _INTERVAL_RE.match("3h")
        assert _INTERVAL_RE.match("1d")
        assert _INTERVAL_RE.match("10s")
        assert _INTERVAL_RE.match("5M") is None  # case-sensitive at regex level

    def test_artifact_whitelist(self):
        # Lock: these are the only files an unauthenticated GET can fetch
        assert _ARTIFACT_WHITELIST == {
            "tearsheet.html",
            "results.json",
            "fills_report.csv",
            "orders_report.csv",
            "positions_report.csv",
            "account_report.csv",
            "order_fills_report.csv",
        }


# ---------------------------------------------------------------------------
# _calc_bars_per_day
# ---------------------------------------------------------------------------


class TestCalcBarsPerDay:
    @pytest.mark.parametrize(
        "interval, expected",
        [
            ("1m", 1440),
            ("5m", 288),
            ("15m", 96),
            ("1h", 24),
            ("4h", 6),
        ],
    )
    def test_known_intervals_fast_path(self, interval: str, expected: int):
        assert _calc_bars_per_day(interval) == expected

    @pytest.mark.parametrize(
        "interval, expected",
        [
            ("2m", 720),
            ("3m", 480),
            ("30m", 48),
            ("2h", 12),
            ("1d", 1),
            ("10s", 8640),
            ("60s", 1440),  # same as 1m
        ],
    )
    def test_computed_intervals(self, interval: str, expected: int):
        assert _calc_bars_per_day(interval) == expected

    def test_case_insensitive(self):
        assert _calc_bars_per_day("5M") == 288
        assert _calc_bars_per_day("3H") == _calc_bars_per_day("3h")

    @pytest.mark.parametrize("interval", ["", "abc", "5x", "5", "-5m", "5mm", "m5"])
    def test_invalid_format_returns_zero(self, interval: str):
        assert _calc_bars_per_day(interval) == 0

    def test_zero_step_returns_zero(self):
        assert _calc_bars_per_day("0m") == 0
        assert _calc_bars_per_day("0h") == 0

    def test_hour_clamped_to_min_one(self):
        # 25h / day → clamped via max(1, ...)
        assert _calc_bars_per_day("25h") == 1

    def test_day_clamped_to_min_one(self):
        assert _calc_bars_per_day("7d") == 1

    def test_2h_matches_12(self):
        """Sanity: 24 / 2 = 12."""
        assert _calc_bars_per_day("2h") == 12


# ---------------------------------------------------------------------------
# _format_estimated_label
# ---------------------------------------------------------------------------


class TestFormatEstimatedLabel:
    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (0, "~0s"),
            (1, "~1s"),
            (59, "~59s"),
            (60, "~1m"),
            (90, "~1m"),           # 90 // 60 = 1
            (120, "~2m"),
            (3599, "~59m"),
            (3600, "~1.0 小时"),
            (5400, "~1.5 小时"),   # 90 minutes
            (36000, "~10.0 小时"),
        ],
    )
    def test_formats(self, seconds: int, expected: str):
        assert _format_estimated_label(seconds) == expected

    def test_boundary_at_60(self):
        """< 60 → seconds; == 60 → minutes."""
        assert _format_estimated_label(59).endswith("s")
        assert _format_estimated_label(60).endswith("m")

    def test_boundary_at_3600(self):
        """< 3600 → minutes; == 3600 → hours."""
        assert _format_estimated_label(3599).endswith("m")
        assert "小时" in _format_estimated_label(3600)


# ---------------------------------------------------------------------------
# Module imports the correct helpers from _utils
# ---------------------------------------------------------------------------


class TestUtilsImports:
    """Ensure the route file uses the shared helpers rather than re-implementing them."""

    def test_uuid_re_imported_from_utils(self):
        from tinohelm.api._utils import UUID_RE as shared
        from tinohelm.api.routes import backtest as mod
        assert mod.UUID_RE is shared

    def test_hex_prefix_re_imported_from_utils(self):
        from tinohelm.api._utils import HEX_PREFIX_RE as shared
        from tinohelm.api.routes import backtest as mod
        assert mod.HEX_PREFIX_RE is shared

    def test_resolve_artifact_path_imported(self):
        from tinohelm.api._utils import resolve_artifact_path as shared
        from tinohelm.api.routes import backtest as mod
        assert mod.resolve_artifact_path is shared

    def test_fetch_redis_progress_imported(self):
        from tinohelm.api._utils import (
            fetch_redis_progress as shared_single,
            fetch_redis_progress_batch as shared_batch,
        )
        from tinohelm.api.routes import backtest as mod
        assert mod.fetch_redis_progress is shared_single
        assert mod.fetch_redis_progress_batch is shared_batch


class TestEstimateRequestDefaults:
    def test_estimate_request_does_not_require_interval(self):
        req = BacktestEstimateRequest(
            symbols=["BTCUSDT-PERP"],
            start_date="2026-01-01",
            end_date="2026-01-02",
        )
        assert req.model_dump() == {
            "symbols": ["BTCUSDT-PERP"],
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
        }

    def test_estimate_uses_1m_source_bars(self):
        req = BacktestEstimateRequest(
            symbols=["BTCUSDT-PERP", "ETHUSDT-PERP"],
            start_date="2026-01-01",
            end_date="2026-01-02",
        )
        resp = asyncio.run(estimate_backtest(req))
        assert resp.total_bars == 1440 * 2


def test_backtest_runner_defaults_do_not_request_extra_replay():
    from tinohelm.backtest.runner import BacktestRunner
    runner = BacktestRunner(symbol="BTCUSDT-PERP", interval="1m")
    assert runner.extra_data_types == []


