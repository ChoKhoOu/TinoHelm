from __future__ import annotations

import sys
from unittest.mock import MagicMock

from nautilus_trader.model import InstrumentId

from tinohelm.strategy.loader import normalize_symbol


class _MoneyLike:
    def __init__(self, value: float):
        self._value = value

    def as_double(self) -> float:
        return self._value


class _InstrumentStub:
    def __init__(self, instrument_id: str):
        self.id = InstrumentId.from_str(instrument_id)
        self.venue = "BINANCE"
        self.quote_currency = "USDT"


def test_trend_pullback_v3_requests_and_subscribes_composite_bars(monkeypatch):
    from importlib.util import module_from_spec, spec_from_file_location

    path = "/root/.tino/strategies/trend_pullback_v3.py"
    spec = spec_from_file_location("trend_pullback_v3_runtime", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class _TrendPullbackV3Stub:
        on_start = module.TrendPullbackV3.on_start

        def __init__(self):
            self.symbols = ["BTCUSDT-PERP"]
            self.interval = "1h"
            self.trend_timeframe = "4h"
            self.daily_timeframe = "1d"
            self._instrument_ids = {}
            self._instruments = {}
            self._bar_types = {}
            self._bar_route = {}
            self._venues = set()
            self._closes_1h = {}
            self._volumes_1h = {}
            self._closes_4h = {}
            self._closes_1d = {}
            self._trend_state_4h = {}
            self._daily_bullish = {}
            self._latest_features = {}
            self._dca_additions = {}
            self._exit_pending = set()
            self.vol_lookback = 150
            self.sv_window = 43
            self.pullback_bars = 4
            self.trend_strength_lookback = 100
            self.cache = MagicMock()
            self.log = MagicMock()
            self.clock = MagicMock()
            self.request_aggregated_bars = MagicMock()
            self.subscribe_bars = MagicMock()

    strategy = _TrendPullbackV3Stub()
    nt_symbol = normalize_symbol("BTCUSDT-PERP")
    instrument = _InstrumentStub(nt_symbol)
    strategy.cache.instrument.return_value = instrument
    strategy.clock.utc_now.return_value = MagicMock()

    monkeypatch.setattr(module, "setup_pause_support", lambda _strategy: None)

    strategy.on_start()

    expected = [
        f"{nt_symbol}-1-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL",
        f"{nt_symbol}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL",
        f"{nt_symbol}-1-DAY-LAST-INTERNAL@1-MINUTE-EXTERNAL",
    ]

    strategy.request_aggregated_bars.assert_called_once()
    requested_bar_types = [str(bt) for bt in strategy.request_aggregated_bars.call_args.args[0]]
    assert requested_bar_types == expected
    assert [str(call.args[0]) for call in strategy.subscribe_bars.call_args_list] == expected


def test_trend_pullback_v3_on_historical_data_updates_same_routes():
    from importlib.util import module_from_spec, spec_from_file_location

    path = "/root/.tino/strategies/trend_pullback_v3.py"
    spec = spec_from_file_location("trend_pullback_v3_runtime_hist", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class _TrendPullbackV3Stub:
        on_historical_data = module.TrendPullbackV3.on_historical_data
        on_bar = module.TrendPullbackV3.on_bar
        _handle_bar = module.TrendPullbackV3._handle_bar
        _refresh_missing_instrument = lambda self, symbol: None
        _build_feature_snapshot = lambda self, symbol: None
        _evaluate_symbol = lambda self, symbol, close: None
        _value_to_float = staticmethod(module.TrendPullbackV3._value_to_float)

        def __init__(self):
            self.interval = "1h"
            self.trend_timeframe = "4h"
            self.daily_timeframe = "1d"
            self._bar_route = {}
            self._closes_1h = {"BTCUSDT-PERP": []}
            self._volumes_1h = {"BTCUSDT-PERP": []}
            self._closes_4h = {"BTCUSDT-PERP": []}
            self._closes_1d = {"BTCUSDT-PERP": []}
            self._trend_state_4h = {"BTCUSDT-PERP": None}
            self._daily_bullish = {"BTCUSDT-PERP": None}
            self._latest_features = {"BTCUSDT-PERP": None}
            self.trend_ema_fast = 6
            self.trend_ema_slow = 18
            self.trend_strength_pctl = 50
            self.trend_strength_lookback = 100

    strategy = _TrendPullbackV3Stub()
    bar_type = module.build_composite_bar_type("BTCUSDT-PERP", "1h")
    strategy._bar_route[str(bar_type)] = ("BTCUSDT-PERP", "1h")

    class _BarLike:
        def __init__(self):
            self.bar_type = bar_type
            self.close = 50000.0
            self.volume = 12.0

    bar = _BarLike()
    strategy.on_historical_data(bar)

    assert strategy._closes_1h["BTCUSDT-PERP"] == [50000.0]
    assert strategy._volumes_1h["BTCUSDT-PERP"] == [12.0]
