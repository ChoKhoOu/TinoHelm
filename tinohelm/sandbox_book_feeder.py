"""Sandbox FILL FUEL — feeds the sim exchange a matching order-book so orders fill.

WHY THIS IS SHARED GLUE (in ``tinohelm/``), not strategy code: it works around a
GENERAL seam in NT's sandbox mode that bites EVERY bar-signal strategy, not a quirk
of any one strategy.

The seam: sandbox mode is "live data client + simulated matching". The
``SandboxExecutionClient`` runs an in-process ``SimulatedExchange`` that has NO data
client of its own — it cannot subscribe or request anything. It only passively
consumes whatever lands on the msgbus pattern ``data.*.{venue}.*`` (=
``data.*.BINANCE.*``, adapters/sandbox/execution.py connect()) via its ``on_data``
handler. So the sim book is only ever as alive as the data some OTHER component
happens to publish on a topic that matches that pattern.

A bar-signal strategy subscribes ONLY bars. NT publishes bars on
``data.bars.{bar_type}`` (= ``data.bars.BTCUSDT-PERP.BINANCE-15-MINUTE-LAST-EXTERNAL``)
— the venue token sits MID-token, so the topic does NOT match ``data.*.BINANCE.*``
(verified via is_matching_py). Bars NEVER reach the sim, its book is never
initialised, and every market order is REJECTED "no market" at submit → zero fills.
``[sandbox] bar_execution=true`` is moot here: the bar can't arrive to be executed.
Neither string is ours to fix (NT hard-codes both the bar topic in data_topics.pyx
and the sim's subscribe pattern in sandbox/execution.py), so the only lever is to
publish an ADDITIONAL feed on a topic that DOES match.

The fix: subscribe L2 order-book deltas per traded instrument. Deltas publish on
``data.book.deltas.BINANCE.{sym}`` — venue is its own dotted segment, and the sandbox
pattern's ``*`` is glob-style (spans dots), so this 5-token topic MATCHES
``data.*.BINANCE.*`` (verified via is_matching_py). ``SimulatedExchange.on_data``
dispatches ``OrderBookDeltas`` to ``process_order_book_deltas``
(adapters/sandbox/execution.py:220), which maintains a real L2 book a market order
fills TAKER against the live best bid/ask.

L2 DELTAS @ 500ms (deliberately NOT quote ticks): deltas drive a genuine L2 book in
the sim rather than only the top of an L1 book, so fills reflect real depth. 500ms is
one of Binance's documented futures depth-stream speeds (valid_speeds=[0,100,250,500];
binance/data.py _subscribe_order_book), passed through NT's generic ``params`` as
``update_speed``. The Binance adapter manages the initial REST snapshot + diff resync
automatically (_order_book_snapshot_then_deltas), so no manual book bootstrap is needed.

REQUIRES the SimulatedExchange ``book_type = L2_MBP`` (set by build_exec_clients from
``[sandbox] book_type``). With L1_MBP the sim only tracks top-of-book and cannot apply
L2 depth deltas correctly.

WIRING: injected automatically by ``build_actor_imports`` whenever ``mode==sandbox``
(opt out per strategy with ``[sandbox] fill_fuel = false`` — e.g. a strategy that
already subscribes quotes/book for its signal, so the sim book is alive without help).
Harmless in live (never injected there). PARITY: pure execution fuel — the strategy
itself is untouched.
"""

from __future__ import annotations

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId

# Binance futures depth-stream update speed (ms). One of the adapter's valid futures
# speeds [0, 100, 250, 500]; passed via NT's generic params → adapter `update_speed`.
# 500ms keeps the sim book fresh without flooding the log/CPU.
BOOK_UPDATE_SPEED_MS = 500


class SandboxBookFeederConfig(ActorConfig, frozen=True):
    """Config for the shared sandbox order-book feeder.

    instrument_ids is OPTIONAL (TinoHelm Option B): when empty, on_start derives the list
    from cache.instrument_ids() — the data client's load_ids universe — so the pool is
    specified ONCE (data_clients.instrument_provider.load_ids in the TOML) and never
    duplicated into this actor's params.
    """

    instrument_ids: list[InstrumentId] = []


class SandboxBookFeeder(Actor):
    """Subscribes L2 order-book deltas per instrument to drive the sim exchange's book."""

    def __init__(self, config: SandboxBookFeederConfig):
        super().__init__(config)
        self._instrument_ids: list[InstrumentId] = list(config.instrument_ids)

    def on_start(self) -> None:
        # Option B: derive from cache when not explicitly configured. Safe — the data
        # client's instrument_provider.initialize() runs inside _connect, which
        # kernel.start_async awaits BEFORE any on_start, so the cache is populated.
        if not self._instrument_ids:
            self._instrument_ids = list(self.cache.instrument_ids())

        for iid in self._instrument_ids:
            # L2 deltas initialise the book the SimulatedExchange fills market orders
            # against. book_type=L2_MBP (must match the sandbox SimulatedExchange's
            # book_type) + update_speed=500ms via NT's generic params (the Binance
            # adapter reads params["update_speed"]; binance/data.py _subscribe_order_book).
            self.subscribe_order_book_deltas(
                iid,
                book_type=BookType.L2_MBP,
                params={"update_speed": BOOK_UPDATE_SPEED_MS},
            )

        self.log.info(
            f"SandboxBookFeeder: subscribed L2 order-book deltas (500ms) for "
            f"{len(self._instrument_ids)} instruments (sim-exchange fill fuel)",
        )

    def on_stop(self) -> None:
        for iid in self._instrument_ids:
            self.unsubscribe_order_book_deltas(iid)
