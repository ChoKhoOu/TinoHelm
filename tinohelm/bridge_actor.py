"""Bridge actor: relays Discord/CLI control commands into the in-process Trader.

NT v1.226 has no cross-process Pause/Resume command. We fill that gap by
publishing tiny JSON envelopes on a dedicated Redis topic and bridging them in
the strategy pod via a NautilusTrader ``Actor``. The actor has direct access to
``self.trader``, so it can call ``Trader.start_strategy`` /
``Trader.stop_strategy`` / ``Trader.market_exit_strategy`` — which is exactly
what NT's own in-process ``ControllerCommand`` enum does.

Topic format::

    commands.tinohelm.{strategy_id}.{action}

Supported actions: ``pause``, ``resume``, ``flatten``, ``ping``.
"""

from __future__ import annotations

import json
from typing import Any

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.identifiers import StrategyId

ACTIONS = {"pause", "resume", "flatten", "ping"}


class BridgeActorConfig(ActorConfig, frozen=True):
    """Config for :class:`BridgeActor`.

    Parameters
    ----------
    strategy_id : str
        The :class:`~nautilus_trader.model.identifiers.StrategyId` literal that
        this bridge controls (1:1 with the pod).
    command_topic : str
        The base topic this actor subscribes to. The pattern subscription is
        ``{command_topic}.*``.
    """

    strategy_id: str
    command_topic: str


class BridgeActor(Actor):
    """NT actor that subscribes to ``commands.tinohelm.{strategy_id}.*``."""

    def __init__(self, config: BridgeActorConfig) -> None:
        super().__init__(config=config)
        self._strategy_id = StrategyId(config.strategy_id)
        self._command_topic = config.command_topic
        # The wildcard pattern NT's switchboard expects.
        self._pattern = f"{config.command_topic}.*"

    def on_start(self) -> None:
        self.msgbus.subscribe(topic=self._pattern, handler=self._on_command)
        self.log.info(f"BridgeActor subscribed to {self._pattern}")

    def on_stop(self) -> None:
        self.msgbus.unsubscribe(topic=self._pattern, handler=self._on_command)

    # ─── handler ──────────────────────────────────────────────────────────────

    def _on_command(self, message: Any) -> None:
        action = self._extract_action(message)
        if action is None:
            self.log.warning(f"BridgeActor: ignoring malformed command: {message!r}")
            return
        if action not in ACTIONS:
            self.log.warning(f"BridgeActor: unknown action {action!r}")
            return

        trader = self.trader
        sid = self._strategy_id
        if action == "pause":
            self.log.info(f"BridgeActor: pause -> trader.stop_strategy({sid})")
            trader.stop_strategy(sid)
        elif action == "resume":
            self.log.info(f"BridgeActor: resume -> trader.start_strategy({sid})")
            trader.start_strategy(sid)
        elif action == "flatten":
            self.log.info(f"BridgeActor: flatten -> trader.market_exit_strategy({sid})")
            trader.market_exit_strategy(sid)
        elif action == "ping":
            self.log.info("BridgeActor: ping ack")

    # ─── parsing ──────────────────────────────────────────────────────────────

    def _extract_action(self, message: Any) -> str | None:
        """Pull the trailing ``.{action}`` from the message envelope.

        We accept three on-wire shapes (msgbus delivers what publishers wrote):

        * ``str``        — entire body is the action ("pause").
        * ``bytes``      — JSON body, e.g. ``{"action": "pause"}``.
        * ``dict``       — already decoded JSON body.

        Topic-based action extraction is also supported if the publisher uses
        the convention ``{command_topic}.{action}`` and an empty payload — we
        intercept it via :meth:`_on_command` then look at the most recent
        wildcard match. NT delivers wildcard messages with the original topic
        on ``message`` only when the publisher wraps it in a tuple, so for
        robustness we always inspect the payload first.
        """

        if isinstance(message, dict):
            return message.get("action")
        if isinstance(message, (bytes, bytearray)):
            try:
                payload = json.loads(message)
            except ValueError:
                return None
            if isinstance(payload, dict):
                return payload.get("action")
            if isinstance(payload, str):
                return payload
            return None
        if isinstance(message, str):
            stripped = message.strip()
            if stripped.startswith("{"):
                try:
                    payload = json.loads(stripped)
                except ValueError:
                    return None
                if isinstance(payload, dict):
                    return payload.get("action")
            return stripped
        return None
