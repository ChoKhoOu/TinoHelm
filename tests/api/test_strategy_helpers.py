"""Tests for pure helpers in tinohelm.api.routes.strategy."""
from __future__ import annotations

import pytest

from tinohelm.api.routes.strategy import (
    StrategySubscription,
    _build_subscriptions,
    _interval_to_timeframe,
)


class TestIntervalToTimeframe:
    @pytest.mark.parametrize(
        "interval, expected",
        [
            ("1m", "1min"),
            ("3m", "3min"),
            ("5m", "5min"),
            ("15m", "15min"),
            ("30m", "30min"),
            ("1h", "1h"),
            ("4h", "4h"),
            ("12h", "12h"),
            ("1d", "1d"),
        ],
    )
    def test_known_intervals(self, interval: str, expected: str):
        assert _interval_to_timeframe(interval) == expected

    def test_unknown_interval_passthrough(self):
        """Unknown intervals pass through unchanged (no raise)."""
        assert _interval_to_timeframe("mystery") == "mystery"
        assert _interval_to_timeframe("") == ""


class TestBuildSubscriptions:
    def test_empty_symbols_returns_empty_list(self):
        assert _build_subscriptions([], "5m") == []

    def test_single_symbol_with_interval(self):
        subs = _build_subscriptions(["BTCUSDT-PERP"], "5m")
        assert len(subs) == 1
        sub = subs[0]
        assert isinstance(sub, StrategySubscription)
        assert sub.exchange == "binance"
        assert sub.symbol == "BTCUSDT-PERP"
        assert sub.granularity == "bar"
        assert sub.timeframe == "5min"
        assert sub.auto is True

    def test_multiple_symbols_all_mapped(self):
        subs = _build_subscriptions(["BTCUSDT-PERP", "ETHUSDT-PERP"], "15m")
        assert [s.symbol for s in subs] == ["BTCUSDT-PERP", "ETHUSDT-PERP"]
        assert all(s.timeframe == "15min" for s in subs)

    def test_strips_binance_suffix(self):
        subs = _build_subscriptions(["BTCUSDT-PERP.BINANCE"], "1h")
        assert subs[0].symbol == "BTCUSDT-PERP"

    def test_interval_none_omits_timeframe(self):
        subs = _build_subscriptions(["BTCUSDT-PERP"], None)
        assert len(subs) == 1
        assert subs[0].timeframe is None
        # Granularity still bar
        assert subs[0].granularity == "bar"

    def test_unknown_interval_passthrough(self):
        subs = _build_subscriptions(["BTCUSDT-PERP"], "7m")
        # 7m is not in the known map; _interval_to_timeframe passes it through
        assert subs[0].timeframe == "7m"
