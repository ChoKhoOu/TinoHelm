"""Tests for pure helpers in tinohelm.api.routes.backtest.

These cover the estimate/label arithmetic that powers POST /api/backtest/estimate
and the module-surface of shared UUID / progress helpers that the route imports
from tinohelm.api._utils.
"""
from __future__ import annotations

import pytest

from tinohelm.api.routes.backtest import (
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


def test_backtest_runner_defaults_do_not_request_extra_replay():
    from tinohelm.backtest.runner import BacktestRunner
    runner = BacktestRunner(symbol="BTCUSDT-PERP", interval="1m")
    assert runner.extra_data_types == []


def test_backtest_runner_remote_catalog_constructor_avoids_from_uri_host_leak(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinohelm.backtest import runner as runner_mod
    from tinohelm.backtest.runner import BacktestRunner

    class FakeCatalog:
        init_calls = []
        from_uri_calls = []

        def __init__(self, catalog_path, fs_protocol=None, fs_storage_options=None, fs_rust_storage_options=None):
            if str(catalog_path).startswith("s3://"):
                raise AssertionError("remote runner must pass bucket/key path, not an s3 URI")
            self.init_calls.append((catalog_path, fs_protocol, fs_storage_options, fs_rust_storage_options))

        @classmethod
        def from_uri(cls, *args, **kwargs):
            cls.from_uri_calls.append((args, kwargs))
            raise AssertionError("from_uri would merge fsspec's host into s3fs options")

    storage = SimpleNamespace(
        provider="s3",
        fs_storage_options={"endpoint_url": "https://example.com"},
        fs_rust_storage_options={"endpoint_url": "https://example.com"},
        uri_for_catalog_root=lambda _path: "s3://bucket/catalog/bar/klines",
    )
    monkeypatch.setattr(runner_mod, "_NT_AVAILABLE", True)
    monkeypatch.setattr(runner_mod, "ParquetDataCatalog", FakeCatalog)

    runner = BacktestRunner(symbol="BTCUSDT-PERP", interval="1m")
    runner._storage = storage

    catalog = runner._catalog_for_path(tmp_path)

    assert isinstance(catalog, FakeCatalog)
    assert FakeCatalog.from_uri_calls == []
    assert FakeCatalog.init_calls == [
        ("bucket/catalog/bar/klines", "s3", storage.fs_storage_options, storage.fs_rust_storage_options)
    ]


def test_backtest_runner_optional_replay_loads_and_injects(monkeypatch):
    from tinohelm.backtest.runner import BacktestRunner

    calls = []
    runner = BacktestRunner(
        symbol="BTCUSDT-PERP",
        interval="1m",
        extra_data_types=["bookTicker", "aggTrades"],
    )

    def fake_load(symbol, source_type):
        calls.append((symbol, source_type))
        return [f"{source_type}-tick"]

    class Engine:
        def __init__(self):
            self.added = []
        def add_data(self, data, sort=False):
            self.added.append((data, sort))

    monkeypatch.setattr(runner, "_load_or_fetch_replay_data", fake_load)
    engine = Engine()
    runner._inject_optional_replay_data(engine)

    assert calls == [("BTCUSDT-PERP", "bookTicker"), ("BTCUSDT-PERP", "trades")]
    assert engine.added == [(["bookTicker-tick"], False), (["trades-tick"], False)]
