"""BridgeActor — bridges NautilusTrader events to Redis PubSub and PostgreSQL."""
from __future__ import annotations

import collections
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import redis

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.message import Event
from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderRejected,
    OrderExpired,
    PositionOpened,
    PositionChanged,
    PositionClosed,
)


def _ts_ns_to_iso(ts_ns: int) -> str:
    """Convert nanosecond timestamp to ISO-8601 string."""
    return datetime.fromtimestamp(ts_ns / 1e9, tz=timezone.utc).isoformat()


class BridgeActorConfig(ActorConfig):
    """Configuration for BridgeActor."""
    redis_url: str = "redis://localhost:6379"
    node_type: str = "sandbox"  # "sandbox" or "live"
    heartbeat_interval_secs: int = 5
    heartbeat_ttl_secs: int = 15
    db_url: str = ""  # Empty string = no DB persistence


class BridgeActor(Actor):
    """Bridges NT MessageBus events to Redis PubSub for external consumption.

    When ``db_url`` is configured, position events are upserted and order
    fills are inserted into PostgreSQL for durable persistence.
    """

    def __init__(self, config: BridgeActorConfig) -> None:
        super().__init__(config)
        self._redis_url = config.redis_url
        self._node_type = config.node_type
        self._hb_interval = config.heartbeat_interval_secs
        self._hb_ttl = config.heartbeat_ttl_secs
        self._db_url = config.db_url or os.environ.get("TINO_DATABASE__URL", "")
        self._redis: redis.Redis | None = None
        self._db_engine: Any = None  # sqlalchemy.Engine | None
        self._cmd_thread: threading.Thread | None = None
        self._cmd_redis: redis.Redis | None = None
        self._cmd_pubsub: Any = None
        self._running = False

        # Command queue replaces the old _flatten_requested bool.
        # Daemon thread appends, NT event loop drains — deque is thread-safe
        # under CPython GIL (atomic append/popleft).
        self._pending_commands: collections.deque = collections.deque()

        # LifecycleController — injected after node.build() via set_lifecycle_deps()
        self._lifecycle: Any = None  # LifecycleController | None
        self._lifecycle_trader: Any = None  # stored until on_start()
        self._lifecycle_risk_engine: Any = None  # stored until on_start()

    def set_lifecycle_deps(self, trader: Any, risk_engine: Any) -> None:
        """Store trader and risk_engine references for LifecycleController.

        Called by ``inject_lifecycle_deps()`` in ``_common.py`` AFTER
        ``node.build()`` so the kernel is ready.
        """
        self._lifecycle_trader = trader
        self._lifecycle_risk_engine = risk_engine

    def on_start(self) -> None:
        self._redis = redis.from_url(self._redis_url)
        self._running = True

        # Initialize sync DB engine for persistence (optional)
        if self._db_url:
            try:
                from tinohelm.db.sync_engine import get_sync_engine
                self._db_engine = get_sync_engine(self._db_url)
                self.log.info("DB persistence enabled")
            except Exception as e:
                self.log.error(f"Failed to init DB engine: {e}")
                self._db_engine = None

        # Instantiate LifecycleController if deps were injected
        if self._lifecycle_trader is not None and self._lifecycle_risk_engine is not None:
            from tinohelm.node.lifecycle_controller import LifecycleController
            self._lifecycle = LifecycleController(
                trader=self._lifecycle_trader,
                risk_engine=self._lifecycle_risk_engine,
                msgbus=self.msgbus,
                log=self.log,
                publish_ack=self._publish,
            )
            self.log.info("LifecycleController initialized")
        else:
            self.log.warning("LifecycleController NOT initialized (no deps injected)")

        # Subscribe to trading events via msgbus wildcard topics
        self.msgbus.subscribe("events.order.*", self._on_order_event)
        self.msgbus.subscribe("events.position.*", self._on_position_event)

        # Subscribe to bar data via MessageBus
        self.msgbus.subscribe(topic="data.bars.*", handler=self._on_bar)

        # Heartbeat timer
        self.clock.set_timer(
            name="bridge_heartbeat",
            interval=timedelta(seconds=self._hb_interval),
            callback=self._send_heartbeat,
        )

        # Command dispatch timer — fires through on_event() so pending
        # commands (set by daemon thread) are processed on the NT event loop.
        self.clock.set_timer(
            name="bridge_cmd_dispatch",
            interval=timedelta(seconds=1),
        )

        # Command listener thread (runs Redis SUBSCRIBE in background)
        self._cmd_thread = threading.Thread(
            target=self._command_listener,
            daemon=True,
        )
        self._cmd_thread.start()

        self.log.info(f"BridgeActor started for {self._node_type}")

    def on_event(self, event: Event) -> None:
        """Handle timer events for command dispatch (runs on NT event loop)."""
        if isinstance(event, TimeEvent) and event.name == "bridge_cmd_dispatch":
            self._drain_pending_commands()

    def _drain_pending_commands(self) -> None:
        """Process all queued commands on the NT event loop thread."""
        while self._pending_commands:
            cmd = self._pending_commands.popleft()
            action = cmd.get("cmd")
            strategy_id = cmd.get("strategy_id")

            if self._lifecycle is None:
                self.log.warning(f"Command '{action}' ignored: LifecycleController not initialized")
                self._publish("commands_ack", {"cmd": action, "status": "error", "reason": "no_lifecycle"})
                continue

            try:
                if action in ("pause", "resume") and not strategy_id:
                    self.log.warning(f"Command '{action}' requires strategy_id")
                    self._publish("commands_ack", {"cmd": action, "status": "error", "reason": "strategy_id required"})
                    continue

                if action == "pause":
                    self._lifecycle.pause_strategy(strategy_id)
                elif action == "resume":
                    self._lifecycle.resume_strategy(strategy_id)
                elif action == "flatten":
                    self._lifecycle.flatten(strategy_id)
                elif action == "halt":
                    self._lifecycle.halt()
                elif action == "unhalt":
                    self._lifecycle.unhalt()
                elif action == "shutdown":
                    self._lifecycle.shutdown()
                else:
                    self.log.warning(f"Unknown command: {action}")
            except Exception as e:
                self.log.error(f"Command '{action}' failed: {e}")
                self._publish("commands_ack", {"cmd": action, "status": "error", "reason": str(e)})

    def on_stop(self) -> None:
        self._running = False
        if self._cmd_pubsub:
            try:
                self._cmd_pubsub.unsubscribe()
            except Exception:
                pass
        if self._cmd_redis:
            try:
                self._cmd_redis.close()
            except Exception:
                pass
        if self._redis:
            self._redis.delete(f"tino:heartbeat:{self._node_type}")
            self._redis.close()
        self.log.info("BridgeActor stopped")

    def _publish(self, channel_suffix: str, data: dict) -> None:
        """Publish event data to Redis PubSub channel."""
        if self._redis:
            channel = f"tino:{self._node_type}:{channel_suffix}"
            try:
                self._redis.publish(channel, json.dumps(data, default=str))
            except Exception as e:
                self.log.error(f"Redis publish error: {e}")

    def _send_heartbeat(self, event: Any) -> None:
        """Publish heartbeat to Redis with TTL."""
        if not self._redis:
            return
        try:
            # Gather status info from cache
            strategies = self.cache.strategy_ids()
            positions = self.cache.positions()

            payload = {
                "ts": str(self.clock.utc_now()),
                "node_type": self._node_type,
                "strategies": len(strategies) if strategies else 0,
                "positions": len(positions) if positions else 0,
            }

            # Enrich with lifecycle state if available
            lc_state: dict = {}
            if self._lifecycle is not None:
                try:
                    lc_state = self._lifecycle.get_state()
                    payload["trading_state"] = lc_state.get("trading_state", "active")
                    payload["strategy_states"] = lc_state.get("strategy_states", {})
                except Exception:
                    payload["trading_state"] = "unknown"
                    payload["strategy_states"] = {}
            else:
                payload["trading_state"] = "active"
                payload["strategy_states"] = {}

            self._redis.setex(
                f"tino:heartbeat:{self._node_type}",
                self._hb_ttl,
                json.dumps(payload, default=str),
            )

            # Also write lifecycle state to a dedicated key for API queries
            self._redis.setex(
                f"tino:{self._node_type}:lifecycle_state",
                self._hb_ttl,
                json.dumps({
                    "trading_state": payload["trading_state"],
                    "strategy_states": payload["strategy_states"],
                    "paused": lc_state.get("paused", []),
                }, default=str),
            )
        except Exception as e:
            self.log.error(f"Heartbeat error: {e}")

    def _command_listener(self) -> None:
        """Background thread listening for commands via Redis PubSub."""
        try:
            r = redis.Redis.from_url(self._redis_url, decode_responses=True)
            ps = r.pubsub()
            self._cmd_redis = r  # store reference for cleanup
            self._cmd_pubsub = ps  # store reference for cleanup
            channel = f"tino:{self._node_type}:commands"
            ps.subscribe(channel)

            while self._running:
                msg = ps.get_message(timeout=1.0)
                if msg is None:
                    continue
                if msg["type"] != "message":
                    continue
                try:
                    cmd = json.loads(msg["data"])
                    self._handle_command(cmd)
                except Exception as e:
                    self.log.error(f"Command parse error: {e}")

            ps.unsubscribe()
            ps.close()
            r.close()
        except Exception as e:
            self.log.error(f"Command listener error: {e}")

    def _handle_command(self, cmd: dict) -> None:
        """Handle incoming commands from API — append to queue for NT thread dispatch."""
        action = cmd.get("cmd")
        self.log.info(f"Received command: {action}")
        # Enqueue for dispatch on NT event loop thread
        self._pending_commands.append(cmd)

    # --- DB persistence helpers ---

    def _persist_position(self, pos: Any) -> None:
        """Upsert position snapshot to the positions table.

        Uses INSERT ... ON CONFLICT(position_id) DO UPDATE for idempotency.
        """
        if not self._db_engine:
            return
        try:
            from sqlalchemy import text
            from sqlalchemy.orm import Session

            position_id = str(pos.id)
            strategy_id_tag = str(pos.strategy_id) if pos.strategy_id else ""
            instrument_id = str(pos.instrument_id)
            side = pos.side.name
            quantity = str(pos.quantity)
            signed_qty = float(pos.signed_qty)
            avg_px_open = float(pos.avg_px_open)
            avg_px_close = float(pos.avg_px_close) if pos.avg_px_close else None
            realized_pnl = pos.realized_pnl.as_double() if pos.realized_pnl else None
            unrealized_pnl = None  # requires a live price; not available here
            currency = str(pos.realized_pnl.currency) if pos.realized_pnl else None
            entry_side = pos.entry.name
            peak_qty = str(pos.peak_qty)
            ts_opened = _ts_ns_to_iso(pos.ts_opened)
            ts_closed = _ts_ns_to_iso(pos.ts_closed) if pos.ts_closed and pos.ts_closed > 0 else None
            duration = str(pos.duration_ns) if pos.duration_ns else None
            is_open = pos.is_open
            event_count = pos.event_count

            stmt = text("""
                INSERT INTO positions (
                    node_type, position_id, strategy_id_tag, instrument_id,
                    side, quantity, signed_qty, avg_px_open, avg_px_close,
                    realized_pnl, unrealized_pnl, currency, entry_side,
                    peak_qty, ts_opened, ts_closed, duration,
                    is_open, event_count
                ) VALUES (
                    :node_type, :position_id, :strategy_id_tag, :instrument_id,
                    :side, :quantity, :signed_qty, :avg_px_open, :avg_px_close,
                    :realized_pnl, :unrealized_pnl, :currency, :entry_side,
                    :peak_qty, :ts_opened, :ts_closed, :duration,
                    :is_open, :event_count
                )
                ON CONFLICT (position_id) DO UPDATE SET
                    side = EXCLUDED.side,
                    quantity = EXCLUDED.quantity,
                    signed_qty = EXCLUDED.signed_qty,
                    avg_px_open = EXCLUDED.avg_px_open,
                    avg_px_close = EXCLUDED.avg_px_close,
                    realized_pnl = EXCLUDED.realized_pnl,
                    unrealized_pnl = EXCLUDED.unrealized_pnl,
                    currency = EXCLUDED.currency,
                    peak_qty = EXCLUDED.peak_qty,
                    ts_closed = EXCLUDED.ts_closed,
                    duration = EXCLUDED.duration,
                    is_open = EXCLUDED.is_open,
                    event_count = EXCLUDED.event_count,
                    updated_at = NOW()
            """)

            with Session(self._db_engine) as session:
                session.execute(stmt, {
                    "node_type": self._node_type,
                    "position_id": position_id,
                    "strategy_id_tag": strategy_id_tag,
                    "instrument_id": instrument_id,
                    "side": side,
                    "quantity": quantity,
                    "signed_qty": signed_qty,
                    "avg_px_open": avg_px_open,
                    "avg_px_close": avg_px_close,
                    "realized_pnl": realized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "currency": currency,
                    "entry_side": entry_side,
                    "peak_qty": peak_qty,
                    "ts_opened": ts_opened,
                    "ts_closed": ts_closed,
                    "duration": duration,
                    "is_open": is_open,
                    "event_count": event_count,
                })
                session.commit()
        except Exception as e:
            self.log.error(f"DB persist position error: {e}")

    def _persist_fill(self, event: OrderFilled) -> None:
        """Insert fill record to the fills table.

        Uses INSERT ... ON CONFLICT(trade_id) DO NOTHING for dedup.
        """
        if not self._db_engine:
            return
        try:
            from sqlalchemy import text
            from sqlalchemy.orm import Session

            trade_id = str(event.trade_id)
            position_id = str(event.position_id) if event.position_id else None
            client_order_id = str(event.client_order_id)
            venue_order_id = str(event.venue_order_id) if event.venue_order_id else None
            strategy_id_tag = str(event.strategy_id) if event.strategy_id else None
            instrument_id = str(event.instrument_id)
            order_side = event.order_side.name
            last_qty = str(event.last_qty)
            last_px = str(event.last_px)
            commission = event.commission.as_double() if event.commission else None
            commission_str = str(commission) if commission is not None else None
            liquidity_side = str(event.liquidity_side.name) if event.liquidity_side else None
            ts_event = _ts_ns_to_iso(event.ts_event)

            stmt = text("""
                INSERT INTO fills (
                    node_type, trade_id, position_id, client_order_id,
                    venue_order_id, strategy_id_tag, instrument_id, order_side,
                    last_qty, last_px, commission, liquidity_side, ts_event
                ) VALUES (
                    :node_type, :trade_id, :position_id, :client_order_id,
                    :venue_order_id, :strategy_id_tag, :instrument_id, :order_side,
                    :last_qty, :last_px, :commission, :liquidity_side, :ts_event
                )
                ON CONFLICT (trade_id) DO NOTHING
            """)

            with Session(self._db_engine) as session:
                session.execute(stmt, {
                    "node_type": self._node_type,
                    "trade_id": trade_id,
                    "position_id": position_id,
                    "client_order_id": client_order_id,
                    "venue_order_id": venue_order_id,
                    "strategy_id_tag": strategy_id_tag,
                    "instrument_id": instrument_id,
                    "order_side": order_side,
                    "last_qty": last_qty,
                    "last_px": last_px,
                    "commission": commission_str,
                    "liquidity_side": liquidity_side,
                    "ts_event": ts_event,
                })
                session.commit()
        except Exception as e:
            self.log.error(f"DB persist fill error: {e}")

    def _build_position_payload(self, pos: Any, event_type: str, ts_event: int) -> dict:
        """Build a rich position payload for Redis publish."""
        return {
            "type": "position.update",
            "event": event_type,
            "node_type": self._node_type,
            "position_id": str(pos.id),
            "strategy_id": str(pos.strategy_id) if pos.strategy_id else None,
            "instrument_id": str(pos.instrument_id),
            "side": pos.side.name,
            "quantity": str(pos.quantity),
            "signed_qty": float(pos.signed_qty),
            "avg_px_open": float(pos.avg_px_open),
            "avg_px_close": float(pos.avg_px_close) if pos.avg_px_close else None,
            "realized_pnl": pos.realized_pnl.as_double() if pos.realized_pnl else 0.0,
            "entry_side": pos.entry.name,
            "peak_qty": str(pos.peak_qty),
            "is_open": pos.is_open,
            "event_count": pos.event_count,
            "ts_opened": _ts_ns_to_iso(pos.ts_opened),
            "ts_closed": _ts_ns_to_iso(pos.ts_closed) if pos.ts_closed and pos.ts_closed > 0 else None,
            "duration_ns": pos.duration_ns if pos.duration_ns else None,
            "ts": _ts_ns_to_iso(ts_event),
        }

    def _build_fill_payload(self, event: OrderFilled) -> dict:
        """Build a rich fill payload for Redis publish."""
        return {
            "type": "fill.new",
            "node_type": self._node_type,
            "trade_id": str(event.trade_id),
            "position_id": str(event.position_id) if event.position_id else None,
            "client_order_id": str(event.client_order_id),
            "venue_order_id": str(event.venue_order_id) if event.venue_order_id else None,
            "strategy_id": str(event.strategy_id) if event.strategy_id else None,
            "instrument_id": str(event.instrument_id),
            "order_side": event.order_side.name,
            "last_qty": str(event.last_qty),
            "last_px": str(event.last_px),
            "commission": event.commission.as_double() if event.commission else 0.0,
            "liquidity_side": str(event.liquidity_side.name) if event.liquidity_side else None,
            "ts": _ts_ns_to_iso(event.ts_event),
        }

    # --- Event dispatchers ---

    def _on_order_event(self, event: Event) -> None:
        """Dispatch order events from msgbus wildcard subscription."""
        if isinstance(event, OrderFilled):
            self._on_order_filled(event)
        elif isinstance(event, OrderAccepted):
            self._on_order_accepted(event)
        elif isinstance(event, OrderRejected):
            self._on_order_rejected(event)
        elif isinstance(event, OrderCanceled):
            self._on_order_canceled(event)
        elif isinstance(event, OrderExpired):
            self._on_order_expired(event)

    def _on_position_event(self, event: Event) -> None:
        """Handle all position events: publish to Redis and persist to DB."""
        if isinstance(event, (PositionOpened, PositionChanged, PositionClosed)):
            pos = event.position
            event_type = type(event).__name__
            payload = self._build_position_payload(pos, event_type, event.ts_event)
            self._publish("positions", payload)
            self._persist_position(pos)

    # --- Event handlers ---

    def _on_bar(self, bar: Bar) -> None:
        """Publish bar data to Redis."""
        self._publish("bars", {
            "event": "bar",
            "bar_type": str(bar.bar_type),
            "instrument_id": str(bar.bar_type.instrument_id),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": str(bar.volume),
            "ts": str(bar.ts_event),
        })

    def _on_order_filled(self, event: OrderFilled) -> None:
        payload = self._build_fill_payload(event)
        self._publish("fills", payload)
        self._persist_fill(event)

    def _on_order_accepted(self, event: OrderAccepted) -> None:
        self._publish("orders", {
            "event": "order_accepted",
            "order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
            "ts": str(event.ts_event),
        })

    def _on_order_rejected(self, event: OrderRejected) -> None:
        self._publish("orders", {
            "event": "order_rejected",
            "order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
            "reason": str(event.reason),
            "ts": str(event.ts_event),
        })

    def _on_order_canceled(self, event: OrderCanceled) -> None:
        self._publish("orders", {
            "event": "order_canceled",
            "order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
            "ts": str(event.ts_event),
        })

    def _on_order_expired(self, event: OrderExpired) -> None:
        self._publish("orders", {
            "event": "order_expired",
            "order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
            "ts": str(event.ts_event),
        })
