"""Behavioral tests for tinohelm.bridge_actor.BridgeActor.

We avoid spinning up a full TradingNode — that's an integration concern. Here
we exercise the public behavior: when a control message arrives on the topic,
the actor must call the corresponding ``Trader`` method exactly once with the
configured ``StrategyId``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nautilus_trader.model.identifiers import StrategyId

from tinohelm.bridge_actor import BridgeActor, BridgeActorConfig


@dataclass
class TraderSpy:
    """A test double recording every Trader method called by the actor.

    ``loaded_ids`` is what ``Trader.strategy_ids()`` returns — the BridgeActor
    resolves the live NT StrategyId from here at command time (it does NOT
    reconstruct it from the control-plane handle, which is just the directory
    name and may not even be a valid StrategyId).
    """

    calls: list[tuple[str, Any]] = field(default_factory=list)
    positions_df: Any = None
    loaded_ids: list[StrategyId] = field(
        default_factory=lambda: [StrategyId("OIMomentum-foo")],
    )

    def strategy_ids(self) -> list[StrategyId]:
        return self.loaded_ids

    def stop_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("stop_strategy", strategy_id))

    def start_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("start_strategy", strategy_id))

    def market_exit_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("market_exit_strategy", strategy_id))

    def generate_positions_report(self) -> Any:
        # ``report`` action funnels through ReportingActor's helper, which
        # calls this method. Returning the configured DataFrame (or None)
        # lets the test pick the empty-vs-rows shape.
        self.calls.append(("generate_positions_report", None))
        return self.positions_df


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
) -> tuple[BridgeActor, TraderSpy, LogSpy]:
    """Create a BridgeActor wired up with spies, bypassing NT registration.

    Calling ``BridgeActor(config)`` would chain into NT's ``Component.__cinit__``
    and demand a registered ``MessageBus`` / ``Cache`` / ``Clock``. Since we're
    only testing the command-dispatch behavior, we sidestep ``__init__`` and
    install just the attributes ``_on_command`` actually reads.

    ``control_handle`` is the TinoHelm control-plane handle (directory name);
    it is deliberately NOT a valid StrategyId (no hyphen) to prove the actor
    does not reconstruct the NT id from it but resolves it from the trader.
    ``_strategy_id`` starts ``None`` so the first command exercises the real
    ``_resolve_strategy_id`` → ``trader.strategy_ids()`` path.
    """

    config = BridgeActorConfig(
        strategy_id=control_handle,
        command_topic=f"commands.tinohelm.{control_handle}",
    )
    actor = BridgeActor.__new__(BridgeActor)
    # Mirror what BridgeActor.__init__ does, sans super().__init__():
    actor._control_handle = config.strategy_id  # type: ignore[attr-defined]
    actor._strategy_id = None  # type: ignore[attr-defined]  # resolved lazily from trader
    actor._command_topic = config.command_topic  # type: ignore[attr-defined]
    actor._pattern = f"{config.command_topic}.*"  # type: ignore[attr-defined]
    trader = trader or TraderSpy()
    log = LogSpy()
    # Patch the @property descriptors so the handler code reads our spies.
    object.__setattr__(actor, "_test_trader", trader)
    object.__setattr__(actor, "_test_log", log)
    object.__setattr__(actor, "_test_msgbus", msgbus or MsgbusSpy())
    actor.__class__ = _PatchedBridgeActor  # swap in a class that returns spies
    return actor, trader, log


class _PatchedBridgeActor(BridgeActor):
    """BridgeActor variant whose ``trader`` and ``log`` attrs read test spies.

    NT's base class declares ``trader``, ``log`` and ``msgbus`` as descriptors
    backed by ``_register_base``. We override all three to return the test
    fixtures so the handler can run without a real TradingNode.
    """

    @property  # type: ignore[override]
    def trader(self) -> Any:
        return self._test_trader  # type: ignore[attr-defined]

    @property  # type: ignore[override]
    def log(self) -> Any:
        return self._test_log  # type: ignore[attr-defined]

    @property  # type: ignore[override]
    def msgbus(self) -> Any:
        return self._test_msgbus  # type: ignore[attr-defined]


# ─── First behavior under TDD ───────────────────────────────────────────────


def test_pause_command_calls_stop_strategy() -> None:
    """Pause envelope → trader.stop_strategy(<NT StrategyId resolved from trader>)."""

    actor, trader, _log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "pause"}).encode("utf-8"))

    # Resolved from trader.strategy_ids(), NOT reconstructed from the "foo" handle.
    assert trader.calls == [("stop_strategy", StrategyId("OIMomentum-foo"))]


def test_resume_command_calls_start_strategy() -> None:
    """Resume envelope → trader.start_strategy(<NT StrategyId resolved from trader>)."""

    actor, trader, _log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "resume"}).encode("utf-8"))

    assert trader.calls == [("start_strategy", StrategyId("OIMomentum-foo"))]


def test_flatten_command_calls_market_exit_strategy() -> None:
    """Flatten envelope → trader.market_exit_strategy(<NT StrategyId resolved from trader>)."""

    actor, trader, _log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "flatten", "reason": "EOD"}).encode("utf-8"))

    assert trader.calls == [("market_exit_strategy", StrategyId("OIMomentum-foo"))]


def test_command_when_no_strategy_loaded_logs_error_and_noops() -> None:
    """If the pod has no strategy yet, a control command must no-op + log error.

    Guards the resolve path: trader.strategy_ids() empty → _resolve_strategy_id
    returns None → handler refuses to call any trader mutator.
    """

    trader = TraderSpy(loaded_ids=[])
    actor, _trader, log = _bridge_actor_under_test("foo", trader=trader)

    actor._on_command(json.dumps({"action": "pause"}).encode("utf-8"))

    assert trader.calls == []
    assert any("no strategy loaded" in e for e in log.errors)


def test_ping_acks_without_calling_trader() -> None:
    """Ping is a liveness probe — must log but never touch ``trader``.

    This is what `tinohelm cli ping --strategy-id ...` relies on: a heartbeat
    that proves the bridge is alive without disturbing positions/strategies.
    """

    actor, trader, log = _bridge_actor_under_test("foo")

    actor._on_command(json.dumps({"action": "ping"}).encode("utf-8"))

    assert trader.calls == []
    assert any("ping" in entry for entry in log.infos)


def test_report_action_publishes_positions_snapshot() -> None:
    """Report envelope → trader.generate_positions_report() + msgbus.publish.

    This is the on-demand counterpart to ReportingActor's 30-min timer; the
    BridgeActor must drive the same helper so the snapshot lands on the
    same topic the periodic snapshot uses (``tinohelm.report.positions``).
    """

    import pandas as pd

    msgbus = MsgbusSpy()
    actor, trader, _log = _bridge_actor_under_test("foo", msgbus=msgbus)
    trader.positions_df = pd.DataFrame(
        {"strategy_id": ["OIMomentum-foo"], "side": ["LONG"], "quantity": [1.0]},
    )

    actor._on_command(json.dumps({"action": "report"}).encode("utf-8"))

    assert ("generate_positions_report", None) in trader.calls
    assert len(msgbus.publishes) == 1
    topic, msg = msgbus.publishes[0]
    assert topic == "tinohelm.report.positions"
    # report uses str(<resolved NT StrategyId>), not the control handle.
    assert msg["strategy_id"] == "OIMomentum-foo"
    assert msg["row_count"] == 1


def test_unknown_action_is_dropped_with_warning() -> None:
    """Garbage / unknown actions must NOT call any trader method.

    Two flavors of garbage:
    * Plain bytes that aren't JSON (and aren't one of the known action words).
    * Well-formed JSON whose ``action`` isn't in ACTIONS.

    Both must result in zero trader calls and a warning log line so an operator
    has a paper trail. This protects us from accidental control commands.
    """

    actor, trader, log = _bridge_actor_under_test("foo")

    actor._on_command(b"not-json{")
    actor._on_command(json.dumps({"action": "drop_database"}).encode("utf-8"))

    assert trader.calls == []
    assert len(log.warnings) >= 1
