"""BridgeActor — bridges NautilusTrader events to Redis PubSub."""
from __future__ import annotations

import json
import threading
from datetime import timedelta
from typing import Any

import redis

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
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
from nautilus_trader.core.message import Event


class BridgeActorConfig(ActorConfig):
    """Configuration for BridgeActor."""
    redis_url: str = "redis://localhost:6379"
    node_type: str = "sandbox"  # "sandbox" or "live"
    heartbeat_interval_secs: int = 5
    heartbeat_ttl_secs: int = 15


class BridgeActor(Actor):
    """Bridges NT MessageBus events to Redis PubSub for external consumption."""

    def __init__(self, config: BridgeActorConfig) -> None:
        super().__init__(config)
        self._redis_url = config.redis_url
        self._node_type = config.node_type
        self._hb_interval = config.heartbeat_interval_secs
        self._hb_ttl = config.heartbeat_ttl_secs
        self._redis: redis.Redis | None = None
        self._cmd_thread: threading.Thread | None = None
        self._running = False

    def on_start(self) -> None:
        self._redis = redis.from_url(self._redis_url)
        self._running = True

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

        # Command listener thread (runs Redis SUBSCRIBE in background)
        self._cmd_thread = threading.Thread(
            target=self._command_listener,
            daemon=True,
        )
        self._cmd_thread.start()

        self.log.info(f"BridgeActor started for {self._node_type}")

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
            # Market exit all strategies
            self.log.warning("FLATTEN command received - closing all positions")
            self._publish("commands_ack", {"cmd": "flatten", "status": "received"})

        elif action == "shutdown":
            self.log.warning("SHUTDOWN command received")
            self._publish("commands_ack", {"cmd": "shutdown", "status": "received"})

        elif action == "stop":
            self.log.info("STOP command received")
            self._publish("commands_ack", {"cmd": "stop", "status": "received"})

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
        self._publish("fills", {
            "event": "order_filled",
            "order_id": str(event.client_order_id),
            "instrument_id": str(event.instrument_id),
            "side": str(event.order_side),
            "quantity": str(event.last_qty),
            "price": str(event.last_px),
            "ts": str(event.ts_event),
        })

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
        self._publish("positions", {
            "event": "position_opened",
            "instrument_id": str(pos.instrument_id),
            "side": str(pos.side),
            "quantity": str(pos.quantity),
            "avg_price": str(pos.avg_px_open),
            "ts": str(event.ts_event),
        })

    def _on_position_changed(self, event: PositionChanged) -> None:
        pos = event.position
        self._publish("positions", {
            "event": "position_changed",
            "instrument_id": str(pos.instrument_id),
            "side": str(pos.side),
            "quantity": str(pos.quantity),
            "unrealized_pnl": str(pos.unrealized_pnl) if pos.unrealized_pnl else "0",
            "realized_pnl": str(pos.realized_pnl) if pos.realized_pnl else "0",
            "ts": str(event.ts_event),
        })

    def _on_position_closed(self, event: PositionClosed) -> None:
        pos = event.position
        self._publish("positions", {
            "event": "position_closed",
            "instrument_id": str(pos.instrument_id),
            "realized_pnl": str(pos.realized_pnl) if pos.realized_pnl else "0",
            "duration": str(pos.duration),
            "ts": str(event.ts_event),
        })
