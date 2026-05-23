"""Smoke-test strategy for TinoHelm wiring.

Subscribes to one instrument's quote ticks, logs each, and emits a
:meth:`Strategy.publish_signal` whenever the spread widens. No order placement
— this is intentionally a no-op trading strategy meant to verify that:

* the strategy pod boots,
* events flow into NT's msgbus,
* the BridgeActor can pause / resume it via Redis.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.config import StrategyConfig
from nautilus_trader.trading.strategy import Strategy


class ExampleStrategyConfig(StrategyConfig, frozen=True, kw_only=True):
    instrument_id: str = "BTCUSDT-PERP.BYBIT"
    spread_threshold_bps: float = 5.0
    log_every_n: int = 100


class ExampleStrategy(Strategy):
    def __init__(self, config: ExampleStrategyConfig) -> None:
        super().__init__(config=config)
        self._instrument_id = InstrumentId.from_str(config.instrument_id)
        self._tick_count = 0
        self._last_signal_value: Decimal | None = None

    def on_start(self) -> None:
        self.log.info(f"ExampleStrategy starting; subscribing to {self._instrument_id}")
        self.subscribe_quote_ticks(self._instrument_id)

    def on_stop(self) -> None:
        self.log.info(f"ExampleStrategy stopping at tick #{self._tick_count}")
        self.unsubscribe_quote_ticks(self._instrument_id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self._tick_count += 1
        if self._tick_count % self.config.log_every_n == 0:
            self.log.info(f"tick #{self._tick_count}: bid={tick.bid_price} ask={tick.ask_price}")

        spread = Decimal(str(tick.ask_price)) - Decimal(str(tick.bid_price))
        if spread <= 0:
            return
        mid = (Decimal(str(tick.ask_price)) + Decimal(str(tick.bid_price))) / 2
        bps = (spread / mid) * Decimal(10_000)
        if bps > Decimal(str(self.config.spread_threshold_bps)) and bps != self._last_signal_value:
            self.publish_signal(name="WideSpread", value=float(bps), ts_event=tick.ts_event)
            self._last_signal_value = bps

    def on_save(self) -> dict[str, bytes]:
        # Persisted by NT's Trader.save() into the cache; restored on next boot
        # via on_load(). Keeping the schema dead simple (str→bytes) so users
        # copying this template don't have to think about serializers.
        return {"tick_count": str(self._tick_count).encode()}

    def on_load(self, state: dict[str, bytes]) -> None:
        raw = state.get("tick_count")
        if raw:
            self._tick_count = int(raw)
