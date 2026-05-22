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
    """A test double recording every Trader method called by the actor."""

    calls: list[tuple[str, Any]] = field(default_factory=list)

    def stop_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("stop_strategy", strategy_id))

    def start_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("start_strategy", strategy_id))

    def market_exit_strategy(self, strategy_id: StrategyId) -> None:
        self.calls.append(("market_exit_strategy", strategy_id))


@dataclass
class LogSpy:
    infos: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)


def _bridge_actor_under_test(strategy_id: str = "FOO-001") -> tuple[BridgeActor, TraderSpy, LogSpy]:
    """Create a BridgeActor wired up with spies, bypassing NT registration.

    Calling ``BridgeActor(config)`` would chain into NT's ``Component.__cinit__``
    and demand a registered ``MessageBus`` / ``Cache`` / ``Clock``. Since we're
    only testing the command-dispatch behavior, we sidestep ``__init__`` and
    install just the attributes ``_on_command`` actually reads.
    """

    config = BridgeActorConfig(
        strategy_id=strategy_id,
        command_topic=f"commands.tinohelm.{strategy_id}",
    )
    actor = BridgeActor.__new__(BridgeActor)
    # Mirror what BridgeActor.__init__ does, sans super().__init__():
    actor._strategy_id = StrategyId(strategy_id)  # type: ignore[attr-defined]
    actor._command_topic = config.command_topic  # type: ignore[attr-defined]
    actor._pattern = f"{config.command_topic}.*"  # type: ignore[attr-defined]
    trader = TraderSpy()
    log = LogSpy()
    # Patch the @property descriptors so the handler code reads our spies.
    object.__setattr__(actor, "_test_trader", trader)
    object.__setattr__(actor, "_test_log", log)
    actor.__class__ = _PatchedBridgeActor  # swap in a class that returns spies
    return actor, trader, log


class _PatchedBridgeActor(BridgeActor):
    """BridgeActor variant whose ``trader`` and ``log`` attrs read test spies.

    NT's base class declares ``trader`` and ``log`` as descriptors backed by
    ``_register_base``. We override both to return the test fixtures so the
    handler can run without a real TradingNode.
    """

    @property  # type: ignore[override]
    def trader(self) -> Any:  # noqa: D401 — NT property override
        return self._test_trader  # type: ignore[attr-defined]

    @property  # type: ignore[override]
    def log(self) -> Any:
        return self._test_log  # type: ignore[attr-defined]


# ─── First behavior under TDD ───────────────────────────────────────────────


def test_pause_command_calls_stop_strategy() -> None:
    """Pause envelope → trader.stop_strategy(StrategyId)."""

    actor, trader, _log = _bridge_actor_under_test("FOO-001")

    actor._on_command(json.dumps({"action": "pause"}).encode("utf-8"))

    assert trader.calls == [("stop_strategy", StrategyId("FOO-001"))]


def test_resume_command_calls_start_strategy() -> None:
    """Resume envelope → trader.start_strategy(StrategyId)."""

    actor, trader, _log = _bridge_actor_under_test("FOO-001")

    actor._on_command(json.dumps({"action": "resume"}).encode("utf-8"))

    assert trader.calls == [("start_strategy", StrategyId("FOO-001"))]


def test_flatten_command_calls_market_exit_strategy() -> None:
    """Flatten envelope → trader.market_exit_strategy(StrategyId)."""

    actor, trader, _log = _bridge_actor_under_test("FOO-001")

    actor._on_command(json.dumps({"action": "flatten", "reason": "EOD"}).encode("utf-8"))

    assert trader.calls == [("market_exit_strategy", StrategyId("FOO-001"))]


def test_ping_acks_without_calling_trader() -> None:
    """Ping is a liveness probe — must log but never touch ``trader``.

    This is what `tinohelm cli ping --strategy-id ...` relies on: a heartbeat
    that proves the bridge is alive without disturbing positions/strategies.
    """

    actor, trader, log = _bridge_actor_under_test("FOO-001")

    actor._on_command(json.dumps({"action": "ping"}).encode("utf-8"))

    assert trader.calls == []
    assert any("ping" in entry for entry in log.infos)


def test_unknown_action_is_dropped_with_warning() -> None:
    """Garbage / unknown actions must NOT call any trader method.

    Two flavors of garbage:
    * Plain bytes that aren't JSON (and aren't one of the known action words).
    * Well-formed JSON whose ``action`` isn't in ACTIONS.

    Both must result in zero trader calls and a warning log line so an operator
    has a paper trail. This protects us from accidental control commands.
    """

    actor, trader, log = _bridge_actor_under_test("FOO-001")

    actor._on_command(b"not-json{")
    actor._on_command(json.dumps({"action": "drop_database"}).encode("utf-8"))

    assert trader.calls == []
    assert len(log.warnings) >= 1
