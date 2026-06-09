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

Supported actions: ``pause``, ``resume``, ``flatten``, ``ping``, ``report``,
``fills``, ``orders``.

``report`` is the on-demand counterpart to :class:`tinohelm.reporting_actor.
ReportingActor`'s 30-min timer — when the operator runs ``/positions``
(or ``tinohelm positions``) the notifier writes a ``report`` command to
this pod's control stream and we synthesize a fresh ``tinohelm.report.
positions`` envelope that flows back through the same channel as the
periodic snapshot.

``fills`` / ``orders`` are the history counterparts (/fills, /orders): same
request/response shape as ``report`` but synthesizing a ``tinohelm.report.
fills`` / ``tinohelm.report.orders`` envelope from NT's ``ReportProvider``
(per-fill and per-order DataFrames over ``cache.orders()``). We never build the
report ourselves — NT owns the schema.
"""

from __future__ import annotations

import json
import os
from typing import Any

from nautilus_trader.common.config import ActorConfig
from nautilus_trader.model.identifiers import StrategyId
from nautilus_trader.trading.controller import Controller

ACTIONS = {"pause", "resume", "flatten", "ping", "report", "fills", "orders"}


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
    mode : str, default "live"
        Run mode (``"live"`` | ``"sandbox"``). Only ``"sandbox"`` (with
        ``sandbox_persist``) gates the restart-recovery hooks; everything else
        leaves on_start/on_stop byte-for-byte unchanged.
    sandbox_persist : bool, default False
        Opt-in (``[sandbox] persist=true``) for sandbox restart recovery: replay
        the last all-currency balance + re-hydrate open orders on start, and
        snapshot the live balance on stop. Off by default so a plain sandbox pod
        stays ephemeral.
    """

    strategy_id: str
    command_topic: str
    mode: str = "live"
    sandbox_persist: bool = False


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
        # has no hyphen) — instead we resolve the live StrategyId from the trader at command
        # time (this pod runs exactly one strategy). Resolved lazily so __init__ stays free
        # of trader access (not yet registered).
        self._control_handle = config.strategy_id
        self._strategy_id: StrategyId | None = None
        # The wildcard pattern NT's switchboard expects.
        self._pattern = f"{config.command_topic}.*"
        # Gate for the sandbox restart-recovery hooks. Stored verbatim so the
        # on_start/on_stop guard is a pure attribute check — when it is not
        # (sandbox + persist) the recovery module is never even imported.
        self._mode = config.mode
        self._sandbox_persist = config.sandbox_persist

    def _resolve_strategy_id(self) -> StrategyId | None:
        """Return this pod's live NT StrategyId (cached). None if no strategy is loaded.

        Read from ``self._trader`` (the trader ref the ``Controller`` base holds)
        rather than the cache. ``Trader._strategies`` is populated the moment
        ``add_strategy`` runs (trader.py), so ``trader.strategy_ids()`` reflects a
        loaded-but-not-yet-trading strategy. ``Cache.strategy_ids()`` would NOT:
        its ``_index_strategies`` only fills on ``trader.save()`` or once an order/
        position exists, so a freshly started pod that hasn't traded yet reads
        empty — which is exactly why ``report``/``pause`` etc. logged "no strategy
        loaded". ``trader.strategy_ids()`` also returns a sorted list (cache returns
        a set), so subscripting ``[0]`` below is safe.
        """
        if self._strategy_id is None:
            ids = self._trader.strategy_ids()
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
        # Sandbox restart recovery (opt-in). NT runs all actors' on_start before
        # any strategy's (trader.py:255-265) and AFTER the build-time
        # initialize_account, so this is the correct moment to replay the last
        # all-currency balance + re-hydrate open orders into the sim — before the
        # strategy can place its first order. live/DEMO never enters this branch,
        # so their on_start is byte-for-byte unchanged.
        if self._recovery_enabled():
            from tinohelm.sandbox_recovery import (
                DEFAULT_BALANCE_KEY_PREFIX,
                recover_on_start,
            )

            recover_on_start(
                trader=self._trader,
                clock=self.clock,
                redis=self._recovery_redis(),
                key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
                trader_id=str(self.trader_id),
            )

    def on_stop(self) -> None:
        self.msgbus.unsubscribe(topic=self._pattern, handler=self._on_command)
        # Snapshot the live all-currency balance so the next boot can replay it
        # over the fresh sim account (NT's initialize_account would otherwise lose
        # it). Same opt-in guard; live/DEMO never enters this branch.
        if self._recovery_enabled():
            from tinohelm.sandbox_recovery import (
                DEFAULT_BALANCE_KEY_PREFIX,
                snapshot_on_stop,
            )

            snapshot_on_stop(
                trader=self._trader,
                redis=self._recovery_redis(),
                key_prefix=DEFAULT_BALANCE_KEY_PREFIX,
                trader_id=str(self.trader_id),
            )

    # ─── sandbox recovery wiring ────────────────────────────────────────────────

    def _recovery_enabled(self) -> bool:
        """The single gate for the recovery hooks: sandbox mode + persist opt-in."""

        return self._mode == "sandbox" and self._sandbox_persist

    def _recovery_redis(self) -> Any:
        """Build a standalone Redis client for the TinoHelm-private balance key.

        We use a dedicated ``redis.Redis.from_url(REDIS_URL)`` (same URL source as
        the CLI's direct XADD and config.py's message-bus assembly), NOT NT's
        private ``cache._database`` handle — the snapshot key lives in TinoHelm's
        own namespace and must not depend on an NT-internal attribute. Imported
        locally so the bridge stays importable from CLI contexts without redis
        eagerly loaded, and only ever constructed inside the recovery guard.
        """

        import redis

        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        return redis.Redis.from_url(url, decode_responses=False)

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
            self._publish_positions_report(sid)
        elif action in ("fills", "orders"):
            self._publish_history_report(action, sid)

    def _publish_positions_report(self, sid: StrategyId) -> None:
        """Synthesize + publish the on-demand positions snapshot (/positions, /pnl)."""

        # Local import keeps bridge_actor importable from CLI contexts that
        # don't ship pandas (the CLI never instantiates this class).
        from tinohelm.reporting_actor import (
            build_positions_report_payload,
            positions_report_df,
            venues_from_cache,
        )

        # The snapshot body is tagged with the CONTROL HANDLE, not str(sid).
        # Unlike pause/resume/flatten — which hand sid to NT's *_from_id and
        # genuinely need the NT StrategyId — the report body is consumed by
        # the notifier, which keys everything (announce registry, /positions
        # listener, channel routing) on the control handle (= file.strategy_id,
        # config.py). ReportingActor's periodic snapshot already tags with the
        # control handle (config.py wires ReportingActorConfig.strategy_id =
        # file.strategy_id), so the on-demand snapshot MUST match or the
        # notifier can't correlate the reply to the waiting /positions future
        # (it lands in #logging and the command spins until it times out).
        self.log.info(
            f"BridgeActor: report -> publish snapshot for {self._control_handle} (strategy {sid})",
        )
        topic, body = build_positions_report_payload(
            positions_report_df(self.cache),
            strategy_id=self._control_handle,
            portfolio=self.portfolio,
            venues=venues_from_cache(self.cache),
        )
        self.msgbus.publish(topic=topic, msg=body)

    def _publish_history_report(self, kind: str, sid: StrategyId) -> None:
        """Synthesize + publish an on-demand history report (/fills or /orders).

        Same request/response contract as ``report``: the body is tagged with
        the CONTROL HANDLE so the notifier can correlate it to the waiting
        ``/fills`` / ``/orders`` future. The DataFrame comes straight from NT's
        ``ReportProvider`` (over ``cache.orders()``) — we own only the transport.
        """

        from tinohelm.reporting_actor import (
            REPORT_TOPIC_FILLS,
            REPORT_TOPIC_ORDERS,
            build_report_payload,
            fills_report_df,
            orders_report_df,
        )

        if kind == "fills":
            df, topic = fills_report_df(self.cache), REPORT_TOPIC_FILLS
        else:
            df, topic = orders_report_df(self.cache), REPORT_TOPIC_ORDERS
        self.log.info(
            f"BridgeActor: {kind} -> publish history for {self._control_handle} (strategy {sid})",
        )
        out_topic, body = build_report_payload(
            df,
            topic=topic,
            strategy_id=self._control_handle,
        )
        self.msgbus.publish(topic=out_topic, msg=body)

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
