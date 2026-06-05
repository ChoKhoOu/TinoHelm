"""Behavioral tests for tinohelm.bridge_actor.BridgeActor.

We avoid spinning up a full TradingNode — that's an integration concern. Here
we exercise the public behavior: when a control message arrives on the topic,
the controller must drive the corresponding strategy-lifecycle call exactly
once with the resolved ``StrategyId``.

BridgeActor subclasses NT's ``Controller``, which holds the trader as
``self._trader`` and exposes ``stop_strategy_from_id`` etc. (each calls
``self._trader.{stop,start,market_exit}_strategy(sid)`` internally). The live
``StrategyId`` is resolved from ``self.cache.strategy_ids()``. So the spies
here are a ``TraderSpy`` (installed as ``_trader``) recording the lifecycle
calls, and a ``CacheSpy`` (installed as ``cache``) supplying the loaded ids.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nautilus_trader.model.identifiers import StrategyId

from tinohelm.bridge_actor import BridgeActor, BridgeActorConfig


@dataclass
class TraderSpy:
    """Records the strategy-lifecycle calls the inherited Controller makes.

    The Controller's ``*_from_id`` methods call ``self._trader.stop_strategy``
    / ``start_strategy`` / ``market_exit_strategy``; this double captures them
    so a test can assert the right method ran with the resolved ``StrategyId``.
    """

    calls: list[tuple[str, Any]] = field(default_factory=list)

    def stop_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("stop_strategy", strategy_id))

    def start_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("start_strategy", strategy_id))

    def market_exit_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("market_exit_strategy", strategy_id))


@dataclass
class CacheSpy:
    """Stand-in for NT's Cache facade.

    ``loaded_ids`` is what ``cache.strategy_ids()`` returns — the BridgeActor
    resolves the live NT StrategyId from here at command time (it does NOT
    reconstruct it from the control-plane handle, which is just the directory
    name and may not even be a valid StrategyId). ``positions`` /
    ``position_snapshots`` feed NT's ``ReportProvider`` on the report path.
    """

    loaded_ids: list[StrategyId] = field(
        default_factory=lambda: [StrategyId("OIMomentum-foo")],
    )
    _positions: list[Any] = field(default_factory=list)
    _snapshots: list[Any] = field(default_factory=list)

    def strategy_ids(self) -> list[StrategyId]:
        return self.loaded_ids

    def positions(self) -> list[Any]:
        return self._positions

    def position_snapshots(self) -> list[Any]:
        return self._snapshots


@dataclass
class MsgbusSpy:
    publishes: list[tuple[str, Any]] = field(default_factory=list)

    def publish(self, *, topic: str, msg: Any) -> None:
        self.publishes.append((topic, msg))


@dataclass
class LogSpy:
    infos: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)


def _bridge_actor_under_test(
    control_handle: str = "foo",
    *,
    msgbus: MsgbusSpy | None = None,
    trader: TraderSpy | None = None,
    cache: CacheSpy | None = None,
) -> tuple[BridgeActor, TraderSpy, CacheSpy, LogSpy]:
    """Create a BridgeActor wired up with spies, bypassing NT registration.

    Calling ``BridgeActor(config, trader)`` would chain into NT's
    ``Component.__cinit__`` and demand a registered ``MessageBus`` / ``Cache``
    / ``Clock``. Since we're only testing the command-dispatch behavior, we
    sidestep ``__init__`` and install just the attributes the handler reads:
    ``_trader`` (a plain attr the inherited ``*_from_id`` methods call) plus
    ``cache`` / ``log`` / ``msgbus`` (NT descriptors, overridden via a patched
    subclass).

    ``control_handle`` is the TinoHelm control-plane handle (directory name);
    it is deliberately NOT a valid StrategyId (no hyphen) to prove the actor
    does not reconstruct the NT id from it but resolves it from the cache.
    ``_strategy_id`` starts ``None`` so the first command exercises the real
    ``_resolve_strategy_id`` → ``cache.strategy_ids()`` path.
    """

    config = BridgeActorConfig(
        strategy_id=control_handle,
        command_topic=f"commands.tinohelm.{control_handle}",
    )
    actor = BridgeActor.__new__(BridgeActor)
    # Mirror what BridgeActor.__init__ does, sans super().__init__():
    actor._control_handle = config.strategy_id  # type: ignore[attr-defined]
    actor._strategy_id = None  # type: ignore[attr-defined]  # resolved lazily from cache
    actor._command_topic = config.command_topic  # type: ignore[attr-defined]
    actor._pattern = f"{config.command_topic}.*"  # type: ignore[attr-defined]
    trader = trader or TraderSpy()
    cache = cache or CacheSpy()
    log = LogSpy()
    # Controller stores the trader as a plain ``self._trader`` attr — set it
    # directly. cache/log/msgbus are NT descriptors, read via the spies below.
    object.__setattr__(actor, "_trader", trader)
    object.__setattr__(actor, "_test_cache", cache)
    object.__setattr__(actor, "_test_log", log)
    object.__setattr__(actor, "_test_msgbus", msgbus or MsgbusSpy())
    actor.__class__ = _PatchedBridgeActor  # swap in a class that returns spies
    return actor, trader, cache, log


class _PatchedBridgeActor(BridgeActor):
    """BridgeActor variant whose ``cache`` / ``log`` / ``msgbus`` read spies.

    NT's base class declares ``cache``, ``log`` and ``msgbus`` as descriptors
    backed by ``_register_base``. We override all three to return the test
    fixtures so the handler can run without a real TradingNode. ``_trader`` is
    a plain instance attr (set in the fixture), so the inherited Controller
    ``*_from_id`` methods reach the TraderSpy without any override here.
    """

    @property  # type: ignore[override]
    def cache(self) -> Any:
        return self._test_cache  # type: ignore[attr-defined]

    @property  # type: ignore[override]
    def log(self) -> Any:
        return self._test_log  # type: ignore[attr-defined]

    @property  # type: ignore[override]
    def msgbus(self) -> Any:
        return self._test_msgbus  # type: ignore[attr-defined]


# ─── First behavior under TDD ───────────────────────────────────────────────


def test_pause_command_calls_stop_strategy() -> None:
    """Pause envelope → trader.stop_strategy(<NT StrategyId resolved from trader>)."""

    actor, trader, _cache, _log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "pause"}).encode("utf-8"))

    # Resolved from cache.strategy_ids(), NOT reconstructed from the "foo" handle.
    assert trader.calls == [("stop_strategy", StrategyId("OIMomentum-foo"))]


def test_resume_command_calls_start_strategy() -> None:
    """Resume envelope → trader.start_strategy(<NT StrategyId resolved from trader>)."""

    actor, trader, _cache, _log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "resume"}).encode("utf-8"))

    assert trader.calls == [("start_strategy", StrategyId("OIMomentum-foo"))]


def test_flatten_command_calls_market_exit_strategy() -> None:
    """Flatten envelope → trader.market_exit_strategy(<NT StrategyId resolved from trader>)."""

    actor, trader, _cache, _log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "flatten", "reason": "EOD"}).encode("utf-8"))

    assert trader.calls == [("market_exit_strategy", StrategyId("OIMomentum-foo"))]


def test_command_when_no_strategy_loaded_logs_error_and_noops() -> None:
    """If the pod has no strategy yet, a control command must no-op + log error.

    Guards the resolve path: trader.strategy_ids() empty → _resolve_strategy_id
    returns None → handler refuses to call any trader mutator.
    """

    cache = CacheSpy(loaded_ids=[])
    actor, trader, _cache, log = _bridge_actor_under_test("foo", cache=cache)

    actor._on_command(json.dumps({"action": "pause"}).encode("utf-8"))

    assert trader.calls == []
    assert any("no strategy loaded" in e for e in log.errors)


def test_ping_acks_without_calling_trader() -> None:
    """Ping is a liveness probe — must log but never touch ``trader``.

    This is what `tinohelm cli ping --strategy-id ...` relies on: a heartbeat
    that proves the bridge is alive without disturbing positions/strategies.
    """

    actor, trader, _cache, log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "ping"}).encode("utf-8"))

    assert trader.calls == []
    assert any("ping" in entry for entry in log.infos)


def test_ping_acks_even_when_no_strategy_loaded() -> None:
    """Ping must ack independently of strategy state.

    Regression: ping is a liveness probe — a pod whose strategy failed to load
    is still a live bridge we want to reach. Previously the handler resolved the
    strategy id before dispatching, so ``trader.strategy_ids()`` empty made ping
    log a spurious "no strategy loaded" error and return WITHOUT acking. Ping is
    now handled before the resolve, so it acks regardless.
    """

    cache = CacheSpy(loaded_ids=[])
    actor, trader, _cache, log = _bridge_actor_under_test("foo", cache=cache)

    actor._on_command(json.dumps({"action": "ping"}).encode("utf-8"))

    assert trader.calls == []
    assert any("ping" in entry for entry in log.infos)
    assert not any("no strategy loaded" in e for e in log.errors)


def test_report_action_publishes_positions_snapshot() -> None:
    """Report envelope → read cache via NT's ReportProvider + msgbus.publish.

    This is the on-demand counterpart to ReportingActor's 30-min timer; the
    BridgeActor must drive the same helper so the snapshot lands on the
    same topic the periodic snapshot uses (``tinohelm.report.positions``),
    tagged with the resolved NT StrategyId (not the control handle). We use an
    empty cache here — building real ``Position`` objects for ``ReportProvider``
    is an integration concern; the row-count/CSV encoding is covered in
    ``test_reporting_actor``. What this test pins is the dispatch contract:
    report → exactly one publish on the right topic with the right id.
    """

    msgbus = MsgbusSpy()
    actor, _trader, _cache, _log = _bridge_actor_under_test("foo", msgbus=msgbus)

    actor._on_command(json.dumps({"action": "report"}).encode("utf-8"))

    assert len(msgbus.publishes) == 1
    topic, msg = msgbus.publishes[0]
    assert topic == "tinohelm.report.positions"
    # body is msgpack bytes (bytes is in NT's _EXTERNAL_PUBLISHABLE_TYPES)
    import msgspec.msgpack
    decoded = msgspec.msgpack.decode(msg)
    # report uses str(<resolved NT StrategyId>), not the control handle.
    assert decoded["strategy_id"] == "OIMomentum-foo"
    assert decoded["row_count"] == 0


def test_unknown_action_is_dropped_with_warning() -> None:
    """Garbage / unknown actions must NOT call any trader method.

    Two flavors of garbage:
    * Plain bytes that aren't JSON (and aren't one of the known action words).
    * Well-formed JSON whose ``action`` isn't in ACTIONS.

    Both must result in zero trader calls and a warning log line so an operator
    has a paper trail. This protects us from accidental control commands.
    """

    actor, trader, _cache, log = _bridge_actor_under_test("foo")

    actor._on_command(b"not-json{")
    actor._on_command(json.dumps({"action": "drop_database"}).encode("utf-8"))

    assert trader.calls == []
    assert len(log.warnings) >= 1
