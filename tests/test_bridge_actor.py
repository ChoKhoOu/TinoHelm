"""Behavioral tests for tinohelm.bridge_actor.BridgeActor.

We avoid spinning up a full TradingNode — that's an integration concern. Here
we exercise the public behavior: when a control message arrives on the topic,
the controller must drive the corresponding strategy-lifecycle call exactly
once with the resolved ``StrategyId``.

BridgeActor subclasses NT's ``Controller``, which holds the trader as
``self._trader`` and exposes ``stop_strategy_from_id`` etc. (each calls
``self._trader.{stop,start,market_exit}_strategy(sid)`` internally). The live
``StrategyId`` is resolved from ``self._trader.strategy_ids()`` — the trader is
the authoritative registry of loaded strategies (``Trader._strategies`` fills on
``add_strategy``), whereas ``Cache.strategy_ids()`` only reflects a strategy once
it has traded or been saved. So ``TraderSpy`` (installed as ``_trader``) both
records the lifecycle calls AND supplies the loaded ids; ``CacheSpy`` (installed
as ``cache``) only feeds NT's ``ReportProvider`` on the report path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nautilus_trader.model.identifiers import StrategyId

from tinohelm.bridge_actor import BridgeActor, BridgeActorConfig


@dataclass
class TraderSpy:
    """Stand-in for NT's ``Trader`` — both the lifecycle target and the id source.

    The Controller's ``*_from_id`` methods call ``self._trader.stop_strategy``
    / ``start_strategy`` / ``market_exit_strategy``; this double captures them
    so a test can assert the right method ran with the resolved ``StrategyId``.

    ``loaded_ids`` is what ``strategy_ids()`` returns — the BridgeActor resolves
    the live NT StrategyId from here at command time. We mirror NT's real
    ``Trader.strategy_ids()`` contract: a **list**, populated the instant a
    strategy is added (independent of whether it has traded). It is NOT
    reconstructed from the control-plane handle, which is just the directory name
    and may not even be a valid StrategyId.
    """

    loaded_ids: list[StrategyId] = field(
        default_factory=lambda: [StrategyId("OIMomentum-foo")],
    )
    calls: list[tuple[str, Any]] = field(default_factory=list)

    def strategy_ids(self) -> list[StrategyId]:
        return self.loaded_ids

    def stop_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("stop_strategy", strategy_id))

    def start_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("start_strategy", strategy_id))

    def market_exit_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("market_exit_strategy", strategy_id))


@dataclass
class CacheSpy:
    """Stand-in for NT's Cache facade — the report path only.

    The BridgeActor does NOT resolve the live StrategyId from the cache: NT's
    ``Cache.strategy_ids()`` only reflects a strategy once it has traded or been
    saved, so a freshly started pod reads empty there. The trader is the id
    source (see ``TraderSpy``). The cache here just feeds NT's ``ReportProvider``
    on the ``report`` path via ``positions_open`` / ``position_snapshots`` —
    the report deliberately reads *open* positions only (closed/FLAT ones must
    not show in a 持仓快照).
    """

    _positions: list[Any] = field(default_factory=list)
    _snapshots: list[Any] = field(default_factory=list)

    def positions_open(self) -> list[Any]:
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


@dataclass
class MsgbusSubSpy(MsgbusSpy):
    """MsgbusSpy that also records subscribe/unsubscribe (for on_start/on_stop)."""

    subs: list[tuple[str, Any]] = field(default_factory=list)
    unsubs: list[tuple[str, Any]] = field(default_factory=list)

    def subscribe(self, *, topic: str, handler: Any) -> None:
        self.subs.append((topic, handler))

    def unsubscribe(self, *, topic: str, handler: Any) -> None:
        self.unsubs.append((topic, handler))


@dataclass
class ClockSpy:
    def timestamp_ns(self) -> int:
        return 1_700_000_000_000_000_000


def _bridge_actor_under_test(
    control_handle: str = "foo",
    *,
    msgbus: MsgbusSpy | None = None,
    trader: TraderSpy | None = None,
    cache: CacheSpy | None = None,
    mode: str = "live",
    sandbox_persist: bool = False,
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
    does not reconstruct the NT id from it but resolves it from the trader.
    ``_strategy_id`` starts ``None`` so the first command exercises the real
    ``_resolve_strategy_id`` → ``trader.strategy_ids()`` path.

    ``mode`` / ``sandbox_persist`` mirror the two recovery-gating fields the
    real ``__init__`` reads off the config; the default (``live`` / ``False``)
    keeps the recovery guard OFF so existing command tests are unaffected.
    """

    config = BridgeActorConfig(
        strategy_id=control_handle,
        command_topic=f"commands.tinohelm.{control_handle}",
        mode=mode,
        sandbox_persist=sandbox_persist,
    )
    actor = BridgeActor.__new__(BridgeActor)
    # Mirror what BridgeActor.__init__ does, sans super().__init__():
    actor._control_handle = config.strategy_id  # type: ignore[attr-defined]
    actor._strategy_id = None  # type: ignore[attr-defined]  # resolved lazily from cache
    actor._pattern = f"{config.command_topic}.*"  # type: ignore[attr-defined]
    actor._mode = config.mode  # type: ignore[attr-defined]
    actor._sandbox_persist = config.sandbox_persist  # type: ignore[attr-defined]
    trader = trader or TraderSpy()
    cache = cache or CacheSpy()
    log = LogSpy()
    # Controller stores the trader as a plain ``self._trader`` attr — set it
    # directly. cache/log/msgbus are NT descriptors, read via the spies below.
    object.__setattr__(actor, "_trader", trader)
    object.__setattr__(actor, "_test_cache", cache)
    object.__setattr__(actor, "_test_log", log)
    object.__setattr__(actor, "_test_msgbus", msgbus or MsgbusSubSpy())
    object.__setattr__(actor, "_test_clock", ClockSpy())
    actor.__class__ = _PatchedBridgeActor  # swap in a class that returns spies
    return actor, trader, cache, log


class _PatchedBridgeActor(BridgeActor):
    """BridgeActor variant whose ``cache`` / ``log`` / ``msgbus`` read spies.

    NT's base class declares ``cache``, ``log``, ``msgbus`` and ``clock`` as
    descriptors backed by ``_register_base``. We override them to return the
    test fixtures so the handler can run without a real TradingNode.
    ``_trader`` is a plain instance attr (set in the fixture), so the inherited
    Controller ``*_from_id`` methods reach the TraderSpy without any override
    here.
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

    @property  # type: ignore[override]
    def clock(self) -> Any:
        return self._test_clock  # type: ignore[attr-defined]


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

    trader = TraderSpy(loaded_ids=[])
    actor, trader, _cache, log = _bridge_actor_under_test("foo", trader=trader)

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

    trader = TraderSpy(loaded_ids=[])
    actor, trader, _cache, log = _bridge_actor_under_test("foo", trader=trader)

    actor._on_command(json.dumps({"action": "ping"}).encode("utf-8"))

    assert trader.calls == []
    assert any("ping" in entry for entry in log.infos)
    assert not any("no strategy loaded" in e for e in log.errors)


def test_report_action_publishes_positions_snapshot() -> None:
    """Report envelope → read cache via NT's ReportProvider + msgbus.publish.

    This is the on-demand counterpart to ReportingActor's 30-min timer; the
    BridgeActor must drive the same helper so the snapshot lands on the
    same topic the periodic snapshot uses (``tinohelm.report.positions``),
    tagged with the CONTROL HANDLE (``file.strategy_id``, here ``"foo"``) — NOT
    the resolved NT StrategyId. This is the cross-process contract: the notifier
    keys its announce registry, /positions listener and channel routing on the
    control handle, and ReportingActor's periodic snapshot tags with the same
    handle (config.py wires both from ``file.strategy_id``). If report tagged
    with ``str(sid)`` instead, the notifier couldn't correlate the reply to the
    waiting /positions future — it would land in #logging and the slash command
    would spin until timeout. We use an empty cache here — building real
    ``Position`` objects for ``ReportProvider`` is an integration concern; the
    row-count/CSV encoding is covered in ``test_reporting_actor``. What this test
    pins is the dispatch contract: report → exactly one publish on the right
    topic tagged with the control handle.
    """

    msgbus = MsgbusSpy()
    # control handle "foo"; trader resolves NT StrategyId "OIMomentum-foo".
    actor, _trader, _cache, _log = _bridge_actor_under_test("foo", msgbus=msgbus)

    actor._on_command(json.dumps({"action": "report"}).encode("utf-8"))

    assert len(msgbus.publishes) == 1
    topic, msg = msgbus.publishes[0]
    assert topic == "tinohelm.report.positions"
    # body is msgpack bytes (bytes is in NT's _EXTERNAL_PUBLISHABLE_TYPES)
    import msgspec.msgpack

    decoded = msgspec.msgpack.decode(msg)
    # report tags the body with the CONTROL HANDLE, not str(<NT StrategyId>),
    # so the notifier's /positions listener (keyed on the handle) matches it.
    assert decoded["strategy_id"] == "foo"
    assert decoded["row_count"] == 0


def test_resolves_from_trader_when_cache_is_empty() -> None:
    """Root-cause regression: a started-but-not-yet-traded strategy resolves fine.

    The live bug: ``_resolve_strategy_id`` read ``cache.strategy_ids()``, whose
    ``_index_strategies`` only fills once the strategy trades or is saved. A
    freshly started pod that hadn't traded yet read empty → every
    pause/resume/flatten/report logged "no strategy loaded" and no-op'd. The fix
    reads ``trader.strategy_ids()`` (populated on ``add_strategy``), so an EMPTY
    cache must NOT block the command. We pin it via ``report``: trader knows the
    id, cache has zero positions → exactly one publish, zero error logs.
    """

    msgbus = MsgbusSpy()
    # Trader has the strategy (as it does the moment add_strategy runs); cache is
    # empty (no orders/positions yet) — the precise on-pod state at startup.
    trader = TraderSpy(loaded_ids=[StrategyId("OIMomentum-foo")])
    cache = CacheSpy()  # no positions / snapshots
    actor, trader, _cache, log = _bridge_actor_under_test(
        "foo",
        msgbus=msgbus,
        trader=trader,
        cache=cache,
    )

    actor._on_command(json.dumps({"action": "report"}).encode("utf-8"))

    assert not any("no strategy loaded" in e for e in log.errors)
    assert len(msgbus.publishes) == 1
    topic, _msg = msgbus.publishes[0]
    assert topic == "tinohelm.report.positions"


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


# ─── sandbox restart recovery guard (mode==sandbox and persist) ─────────────


def test_live_mode_on_start_stop_never_runs_recovery(monkeypatch: Any) -> None:
    """live/DEMO (the default) → on_start/on_stop must NOT touch recovery glue.

    This is the zero-impact contract: the guard ``mode=="sandbox" and persist``
    must be the ONLY thing that can route into ``sandbox_recovery``. In live the
    bridge's on_start/on_stop behave exactly as before — only the msgbus
    subscribe/unsubscribe happen.
    """

    import tinohelm.sandbox_recovery as recovery

    calls: list[str] = []
    monkeypatch.setattr(recovery, "recover_on_start", lambda **_k: calls.append("recover"))
    monkeypatch.setattr(recovery, "snapshot_on_stop", lambda **_k: calls.append("snapshot"))

    actor, _trader, _cache, _log = _bridge_actor_under_test("foo")  # mode="live"

    actor.on_start()
    actor.on_stop()

    assert calls == []
    # And the normal command-bridge subscription still happened.
    assert actor.msgbus.subs  # type: ignore[attr-defined]
    assert actor.msgbus.unsubs  # type: ignore[attr-defined]


def test_sandbox_mode_without_persist_never_runs_recovery(monkeypatch: Any) -> None:
    """sandbox but persist=False (ephemeral, the existing default) → no recovery.

    Recovery is opt-in via ``[sandbox] persist=true``; a plain sandbox pod stays
    ephemeral and must not replay balances or re-hydrate orders.
    """

    import tinohelm.sandbox_recovery as recovery

    calls: list[str] = []
    monkeypatch.setattr(recovery, "recover_on_start", lambda **_k: calls.append("recover"))
    monkeypatch.setattr(recovery, "snapshot_on_stop", lambda **_k: calls.append("snapshot"))

    actor, _trader, _cache, _log = _bridge_actor_under_test(
        "foo",
        mode="sandbox",
        sandbox_persist=False,
    )

    actor.on_start()
    actor.on_stop()

    assert calls == []


def test_sandbox_persist_on_start_runs_recover_on_stop_runs_snapshot(monkeypatch: Any) -> None:
    """sandbox + persist=True → on_start calls recover_on_start, on_stop snapshot.

    Pins the wiring contract: the bridge forwards its own trader + clock to the
    recovery helpers (the helpers themselves are spy-tested in
    test_sandbox_recovery). We assert each helper fires exactly once with the
    trader the Controller holds.
    """

    import tinohelm.sandbox_recovery as recovery

    recover_calls: list[dict[str, Any]] = []
    snapshot_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(recovery, "recover_on_start", lambda **kw: recover_calls.append(kw))
    monkeypatch.setattr(recovery, "snapshot_on_stop", lambda **kw: snapshot_calls.append(kw))

    actor, trader, _cache, _log = _bridge_actor_under_test(
        "foo",
        mode="sandbox",
        sandbox_persist=True,
    )

    actor.on_start()
    assert len(recover_calls) == 1
    assert recover_calls[0]["trader"] is trader
    assert "redis" in recover_calls[0]
    assert "clock" in recover_calls[0]

    actor.on_stop()
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0]["trader"] is trader
    assert "redis" in snapshot_calls[0]
