"""Bridge controller: relays Discord/CLI control commands into the Trader.

NT has no cross-process Pause/Resume command. We fill that gap by publishing
tiny JSON envelopes on a dedicated Redis topic and bridging them in the
strategy pod via a NautilusTrader :class:`~nautilus_trader.trading.controller.
Controller`. A plain ``Actor`` cannot do this: it exposes ``self.cache`` /
``self.portfolio`` but NOT a trader reference — only the ``Controller`` subclass
is constructed with one (``Controller.__init__(self, trader, config)`` stores
``self._trader``) and exposes the ``start_strategy_from_id`` /
``stop_strategy_from_id`` / ``market_exit_strategy_from_id`` methods we need.
That is exactly NT's own in-process control surface, so we subclass it rather
than reach for an attribute the base ``Actor`` never had.

Topic format::

    commands.tinohelm.{strategy_id}.{action}

Supported actions: ``pause``, ``resume``, ``flatten``, ``ping``, ``report``.

``report`` is the on-demand counterpart to :class:`tinohelm.reporting_actor.
ReportingActor`'s 30-min timer — when the operator runs ``/positions``
(or ``tinohelm positions``) the notifier writes a ``report`` command to
this pod's control stream and we synthesize a fresh ``tinohelm.report.
positions`` envelope that flows back through the same channel as the
periodic snapshot.
"""

from __future__ import annotations

import json
from typing import Any

from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.trading.controller import Controller

ACTIONS = {"pause", "resume", "flatten", "ping", "report"}


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


class BridgeActor(Controller):
    """NT controller that subscribes to ``commands.tinohelm.{strategy_id}.*``.

    A :class:`Controller` is an :class:`Actor` that NT constructs with a
    ``trader`` ref. NT's kernel wires it from ``TradingNodeConfig.controller``
    via ``ControllerFactory.create(config, trader)`` →
    ``controller_cls(config=config, trader=trader)`` — hence the ``(config,
    trader)`` signature here. We delegate strategy lifecycle to the inherited
    ``*_from_id`` methods (which call ``self._trader`` internally) and never
    reimplement what the base class already does.
    """

    def __init__(self, config: BridgeActorConfig, trader: Any) -> None:
        super().__init__(trader=trader, config=config)
        # config.strategy_id is the TinoHelm CONTROL-PLANE handle (= the strategy
        # directory name, e.g. "oi_momentum_lowvol"), NOT the NT StrategyId. NT derives
        # its own id as "{StrategyClassName}-{order_id_tag}" (config.py:build_strategy_imports),
        # which is what the Controller's *_from_id methods require. We do NOT try to
        # reconstruct that here (the control handle may not even be a valid StrategyId — it
        # has no hyphen) — instead we resolve the live StrategyId from the cache at command
        # time (this pod runs exactly one strategy). Resolved lazily so __init__ stays free
        # of cache access (not yet registered).
        self._control_handle = config.strategy_id
        self._strategy_id: StrategyId | None = None
        # The wildcard pattern NT's switchboard expects.
        self._pattern = f"{config.command_topic}.*"

    def _resolve_strategy_id(self) -> StrategyId | None:
        """Return this pod's live NT StrategyId (cached). None if no strategy is loaded.

        Read from ``self.cache`` (an ``Actor`` facade NT registers on every
        component) rather than the trader — the cache is the canonical source of
        loaded strategy ids and keeps this independent of the trader API surface.
        """
        if self._strategy_id is None:
            ids = self.cache.strategy_ids()
            if not ids:
                return None
            if len(ids) > 1:
                # TinoHelm pods are 1:1 with a strategy; warn but act on the first.
                self.log.warning(
                    f"BridgeActor: expected 1 strategy in this pod, found {len(ids)}: {ids}",
                )
            self._strategy_id = ids[0]
        return self._strategy_id

    def on_start(self) -> None:
        self.msgbus.subscribe(topic=self._pattern, handler=self._on_command)
        self.log.info(
            f"BridgeActor subscribed to {self._pattern} (control handle={self._control_handle})",
        )

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

        # ``ping`` is a liveness probe: it only confirms this BridgeActor is alive
        # and processing commands, so it MUST be independent of whether a strategy
        # is loaded (a pod whose strategy failed to load is still a live bridge we
        # want to be able to reach). Ack it before resolving the strategy id — the
        # remaining actions all act on a strategy and genuinely need ``sid``.
        if action == "ping":
            self.log.info("BridgeActor: ping ack")
            return

        sid = self._resolve_strategy_id()
        if sid is None:
            self.log.error(
                f"BridgeActor: no strategy loaded in this pod; cannot apply {action!r}",
            )
            return
        # The Controller base owns the trader; its *_from_id methods call
        # self._trader.{stop,start,market_exit}_strategy(sid) internally. We
        # delegate rather than touch the trader directly.
        if action == "pause":
            self.log.info(f"BridgeActor: pause -> stop_strategy_from_id({sid})")
            self.stop_strategy_from_id(sid)
        elif action == "resume":
            self.log.info(f"BridgeActor: resume -> start_strategy_from_id({sid})")
            self.start_strategy_from_id(sid)
        elif action == "flatten":
            self.log.info(f"BridgeActor: flatten -> market_exit_strategy_from_id({sid})")
            self.market_exit_strategy_from_id(sid)
        elif action == "report":
            # Local import keeps bridge_actor importable from CLI contexts that
            # don't ship pandas (the CLI never instantiates this class).
            from tinohelm.reporting_actor import (
                build_positions_report_payload,
                positions_report_df,
                venues_from_cache,
            )

            self.log.info(f"BridgeActor: report -> publish snapshot for {sid}")
            topic, body = build_positions_report_payload(
                positions_report_df(self.cache),
                strategy_id=str(sid),
                portfolio=self.portfolio,
                venues=venues_from_cache(self.cache),
            )
            self.msgbus.publish(topic=topic, msg=body)

    # ─── parsing ──────────────────────────────────────────────────────────────

    def _extract_action(self, message: Any) -> str | None:
        """Read the ``action`` field out of the command payload.

        We accept three on-wire shapes (msgbus delivers what publishers wrote):

        * ``dict``  — primary path: CLI writes msgpack, NT's MsgSpecSerializer
                      decodes it to a plain dict before publish_bus_message
                      delivers it here (no ``type`` tag → deserialize returns
                      obj_dict as-is). ``action`` is read straight off the dict.
        * ``bytes`` — fallback for callers that publish raw bytes directly onto
                      the msgbus (e.g. test helpers or future custom publishers).
                      Attempted as JSON first for backwards-compat.
        * ``str``   — action name written directly, e.g. ``"pause"``.

        Returns ``None`` for anything we can't parse to a known shape; the
        caller logs and drops it.
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
