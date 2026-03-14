"""BridgeActor — bridges NautilusTrader events to Redis PubSub and PostgreSQL."""
from __future__ import annotations

import json
import os
import signal as _signal
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
        self._running = False
        self._flatten_requested = False  # Atomic flag for thread-safe flatten dispatch

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

        # Subscribe to trading events
        self.register_event_handler(OrderFilled, self._on_order_filled)
        self.register_event_handler(OrderAccepted, self._on_order_accepted)
        self.register_event_handler(OrderRejected, self._on_order_rejected)
        self.register_event_handler(OrderCanceled, self._on_order_canceled)
        self.register_event_handler(OrderExpired, self._on_order_expired)
        self.register_event_handler(PositionOpened, self._on_position_opened)
        self.register_event_handler(PositionChanged, self._on_position_changed)
        self.register_event_handler(PositionClosed, self._on_position_closed)

        # Subscribe to bar data via MessageBus
        self.msgbus.subscribe(topic="data.bars.*", handler=self._on_bar)

        # Heartbeat timer
        self.clock.set_timer(
            name="bridge_heartbeat",
            interval=timedelta(seconds=self._hb_interval),
            callback=self._send_heartbeat,
        )

        # Command dispatch timer — no callback on purpose: fires through on_event()
        # so the flatten flag (set by daemon thread) is checked on the NT event
        # loop thread, making msgbus.publish() thread-safe.
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
            if self._flatten_requested:
                self._flatten_requested = False
                self.log.warning("Executing flatten — closing all positions via msgbus")
                self.msgbus.publish("risk.flatten", "flatten_all")

    def on_stop(self) -> None:
        self._running = False
        if hasattr(self, '_cmd_pubsub') and self._cmd_pubsub:
            try:
                self._cmd_pubsub.unsubscribe()
            except Exception:
                pass
        if hasattr(self, '_cmd_redis') and self._cmd_redis:
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
            self._redis.setex(
                f"tino:heartbeat:{self._node_type}",
                self._hb_ttl,
                json.dumps(payload, default=str),
            )
        except Exception as e:
            self.log.error(f"Heartbeat error: {e}")

    def _command_listener(self) -> None:
        """Background thread listening for commands via Redis PubSub."""
        try:
            import redis as sync_redis
            r = sync_redis.Redis.from_url(self._redis_url, decode_responses=True)
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
        """Handle incoming commands from API."""
        action = cmd.get("cmd")
        self.log.info(f"Received command: {action}")

        if action == "pause":
            strategy_id = cmd.get("strategy_id")
            if strategy_id:
                # Stop specific strategy
                for strategy in self.cache.strategies():
                    if str(strategy.id) == strategy_id:
                        self.log.info(f"Pausing strategy: {strategy_id}")
                        # The trader will handle this
                        break
            self._publish("commands_ack", {"cmd": "pause", "status": "received"})

        elif action == "flatten":
            # Schedule flatten on the NT event loop via atomic flag
            self.log.warning("FLATTEN command received - scheduling position exit")
            self._flatten_requested = True
            self._publish("commands_ack", {"cmd": "flatten", "status": "scheduled"})

        elif action == "shutdown":
            self.log.warning("SHUTDOWN command received - initiating node shutdown")
            self._publish("commands_ack", {"cmd": "shutdown", "status": "received"})
            os.kill(os.getpid(), _signal.SIGTERM)

        elif action == "stop":
            self.log.info("STOP command received")
            self._publish("commands_ack", {"cmd": "stop", "status": "received"})

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

    def _on_position_opened(self, event: PositionOpened) -> None:
        pos = event.position
        payload = self._build_position_payload(pos, "position_opened", event.ts_event)
        self._publish("positions", payload)
        self._persist_position(pos)

    def _on_position_changed(self, event: PositionChanged) -> None:
        pos = event.position
        payload = self._build_position_payload(pos, "position_changed", event.ts_event)
        self._publish("positions", payload)
        self._persist_position(pos)

    def _on_position_closed(self, event: PositionClosed) -> None:
        pos = event.position
        payload = self._build_position_payload(pos, "position_closed", event.ts_event)
        self._publish("positions", payload)
        self._persist_position(pos)
