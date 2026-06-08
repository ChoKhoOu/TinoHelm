"""Tests for the sandbox-only order-book feeder.

In sandbox mode the in-process ``SimulatedExchange`` fills market orders against
an L1/L2 book it maintains from a matching data feed (msgbus pattern
``data.*.BINANCE.*``). This actor subscribes that feed PER traded instrument so
the sim book is initialised and orders fill instead of being rejected "no market".

The feeder uses **L2 order-book deltas at 500ms** (Binance futures diff stream)
rather than quote ticks — deltas drive a true L2 book in the sim, and 500ms is
one of Binance's documented futures depth-stream speeds.

These behaviours are testable without a TradingNode: the actor's ``on_start``
reads ``cache.instrument_ids()`` and calls NT ``subscribe_*`` methods, which we
override in a patched subclass to record the calls. We follow the same
spy-subclass + ``__new__`` pattern as ``test_bridge_actor.py`` to bypass NT's
``Component.__cinit__`` (which would demand a registered MessageBus/Cache/Clock).
"""

from __future__ import annotations

from typing import Any

from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId

from tinohelm.sandbox_book_feeder import (
    SandboxBookFeeder,
    SandboxBookFeederConfig,
)

# Binance futures depth-stream update speed the feeder must request (one of the
# adapter's valid futures speeds [0, 100, 250, 500] ms; binance/data.py
# _subscribe_order_book). 500ms keeps the sim book fresh without flooding.
EXPECTED_UPDATE_SPEED_MS = 500


class _LogSpy:
    def info(self, *_a: Any, **_k: Any) -> None: ...
    def warning(self, *_a: Any, **_k: Any) -> None: ...
    def error(self, *_a: Any, **_k: Any) -> None: ...


class _CacheSpy:
    """Stand-in for NT's Cache — returns a fixed instrument universe."""

    def __init__(self, instrument_ids: list[InstrumentId]) -> None:
        self._ids = instrument_ids

    def instrument_ids(self) -> list[InstrumentId]:
        return list(self._ids)


class _PatchedFeeder(SandboxBookFeeder):
    """Feeder variant that records subscribe calls and reads spy cache/log.

    NT's base ``Actor`` declares ``cache``/``log`` as descriptors backed by
    ``_register_base`` and ``subscribe_*`` as Cython methods. We override them in
    Python so ``on_start`` runs without a real TradingNode.
    """

    @property  # type: ignore[override]
    def cache(self) -> Any:
        return self._test_cache  # type: ignore[attr-defined]

    @property  # type: ignore[override]
    def log(self) -> Any:
        return self._test_log  # type: ignore[attr-defined]

    def subscribe_order_book_deltas(self, instrument_id: InstrumentId, **kwargs: Any) -> None:  # type: ignore[override]
        self.delta_subs.append((instrument_id, kwargs))  # type: ignore[attr-defined]

    def subscribe_quote_ticks(self, instrument_id: InstrumentId, **kwargs: Any) -> None:  # type: ignore[override]
        self.quote_subs.append((instrument_id, kwargs))  # type: ignore[attr-defined]

    def unsubscribe_order_book_deltas(self, instrument_id: InstrumentId, **kwargs: Any) -> None:  # type: ignore[override]
        self.delta_unsubs.append((instrument_id, kwargs))  # type: ignore[attr-defined]


def _feeder_under_test(
    instrument_ids: list[InstrumentId],
    *,
    configured_ids: list[InstrumentId] | None = None,
) -> _PatchedFeeder:
    """Build a patched feeder with a spy cache, bypassing NT registration."""

    config = SandboxBookFeederConfig(instrument_ids=configured_ids or [])
    feeder = _PatchedFeeder.__new__(_PatchedFeeder)
    # Mirror SandboxBookFeeder.__init__ sans super().__init__():
    feeder._instrument_ids = list(config.instrument_ids)  # type: ignore[attr-defined]
    feeder.delta_subs = []  # type: ignore[attr-defined]
    feeder.quote_subs = []  # type: ignore[attr-defined]
    feeder.delta_unsubs = []  # type: ignore[attr-defined]
    object.__setattr__(feeder, "_test_cache", _CacheSpy(instrument_ids))
    object.__setattr__(feeder, "_test_log", _LogSpy())
    return feeder


# ─── Behaviour under test ───────────────────────────────────────────────────


def test_on_start_subscribes_l2_deltas_per_instrument_at_500ms() -> None:
    """on_start must subscribe L2_MBP order-book deltas at 500ms for each
    instrument — and NOT quote ticks (the feed source changed quote→delta)."""

    ids = [
        InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
    ]
    feeder = _feeder_under_test(ids)

    feeder.on_start()

    # One delta subscription per instrument, in order; zero quote subscriptions.
    assert [iid for iid, _ in feeder.delta_subs] == ids
    assert feeder.quote_subs == []

    for _iid, kwargs in feeder.delta_subs:
        assert kwargs["book_type"] == BookType.L2_MBP
        assert kwargs["params"] == {"update_speed": EXPECTED_UPDATE_SPEED_MS}


def test_on_start_derives_universe_from_cache_when_unconfigured() -> None:
    """Option B: with no configured instrument_ids, derive the pool from
    cache.instrument_ids() (the data client's load_ids universe)."""

    ids = [InstrumentId.from_str("SOLUSDT-PERP.BINANCE")]
    feeder = _feeder_under_test(ids, configured_ids=[])

    feeder.on_start()

    assert [iid for iid, _ in feeder.delta_subs] == ids


def test_on_stop_unsubscribes_each_instrument() -> None:
    """on_stop must release every delta subscription it opened."""

    ids = [
        InstrumentId.from_str("BTCUSDT-PERP.BINANCE"),
        InstrumentId.from_str("ETHUSDT-PERP.BINANCE"),
    ]
    feeder = _feeder_under_test(ids)

    feeder.on_start()
    feeder.on_stop()

    assert [iid for iid, _ in feeder.delta_unsubs] == ids
