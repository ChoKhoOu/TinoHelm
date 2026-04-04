"""SnapshotActor — bridges NT events to Redis PubSub for external consumers.

Subscribes to position, order, bar, strategy snapshot, and risk metric events,
builds JSON payloads, and publishes to Redis PubSub + ring buffers.
Also includes a rate-limited RedisLogHandler for log forwarding.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis

from nautilus_trader.common.actor import Actor
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.message import Event
from nautilus_trader.model.data import Bar
from nautilus_trader.model.events import (
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderRejected,
    PositionChanged,
    PositionClosed,
    PositionOpened,
)

from tinohelm.node.actors._utils import redis_publish, ts_ns_to_iso


class SnapshotActorConfig(ActorConfig):
    redis_url: str = "redis://localhost:6379"
    node_type: str = "sandbox"


class _RedisLogHandler(logging.Handler):
    """Publishes log records to Redis with token bucket rate limiting."""

    def __init__(self, redis_client: redis.Redis, node_type: str, rate_limit: int = 10):
        super().__init__()
        self._redis = redis_client
        self._node_type = node_type
        self._tokens = float(rate_limit)
        self._last_refill = 0.0
        self._rate_limit = rate_limit

    def emit(self, record: logging.LogRecord) -> None:
        import time as _time
        now = _time.monotonic()
        if self._last_refill == 0.0:
            self._last_refill = now
        elapsed = now - self._last_refill
        self._tokens = min(self._rate_limit, self._tokens + elapsed * self._rate_limit)
        self._last_refill = now
        if self._tokens < 1:
            return
        self._tokens -= 1
        try:
            payload = json.dumps({
                "type": "log.entry",
                "node_type": self._node_type,
                "level": record.levelname,
                "message": record.getMessage(),
                "logger_name": record.name,
                "ts": ts_ns_to_iso(int(_time.time() * 1e9)),
            })
            self._redis.publish(f"tino:{self._node_type}:logs", payload)
        except Exception:
            pass


class SnapshotActor(Actor):
    """Bridges NT internal events to Redis PubSub for frontend/TUI consumption."""

    def __init__(self, config: SnapshotActorConfig) -> None:
        super().__init__(config)
        self._redis_url = config.redis_url
        self._node_type = config.node_type
        self._redis: redis.Redis | None = None
        self._log_handler: _RedisLogHandler | None = None

    def on_start(self) -> None:
        self._redis = redis.from_url(self._redis_url)

        # Subscribe to trading events via msgbus wildcard topics
        self.msgbus.subscribe("events.order.*", self._on_order_event)
        self.msgbus.subscribe("events.position.*", self._on_position_event)
        self.msgbus.subscribe(topic="data.bars.*", handler=self._on_bar)

        # Subscribe to risk metrics from RiskGuardActor
        self.msgbus.subscribe("risk.metrics.snapshot", self._on_risk_metrics)

        # Subscribe to strategy signal snapshots
        from nautilus_trader.model.data import DataType
        from tinohelm.data.strategy_snapshot import StrategySnapshot
        self.subscribe_data(DataType(StrategySnapshot))

        # Install rate-limited log handler
        handler = _RedisLogHandler(self._redis, self._node_type)
        handler.setLevel(logging.INFO)
        logging.getLogger("tinohelm").addHandler(handler)
        self._log_handler = handler

        self.log.info(f"SnapshotActor started for {self._node_type}")

    def on_data(self, data: Any) -> None:
        from tinohelm.data.strategy_snapshot import StrategySnapshot
        if isinstance(data, StrategySnapshot):
            self._on_strategy_snapshot(data)

    def on_stop(self) -> None:
        if self._log_handler:
            logging.getLogger("tinohelm").removeHandler(self._log_handler)
            self._log_handler = None
        if self._redis:
            self._redis.close()

    # --- Publish helper ---

    def _publish(self, channel_suffix: str, data: dict) -> None:
        redis_publish(self._redis, self._node_type, channel_suffix, data)

    # --- Position events (all types for real-time frontend updates) ---

    def _on_position_event(self, event: Event) -> None:
        if isinstance(event, (PositionOpened, PositionChanged, PositionClosed)):
            pos = event.position
            payload = self._build_position_payload(pos, type(event).__name__, event.ts_event)
            self._publish("positions", payload)

    def _build_position_payload(self, pos: Any, event_type: str, ts_event: int) -> dict:
        strategy_id_str = str(pos.strategy_id) if pos.strategy_id else ""
        return {
            "type": "position.update",
            "event": event_type,
            "node_type": self._node_type,
            "id": 0,
            "position_id": str(pos.id),
            "strategy_id": strategy_id_str,
            "strategy_id_tag": strategy_id_str,
            "instrument_id": str(pos.instrument_id),
            "side": pos.side.name,
            "quantity": str(pos.quantity),
            "signed_qty": float(pos.signed_qty),
            "avg_px_open": float(pos.avg_px_open),
            "avg_px_close": float(pos.avg_px_close) if pos.avg_px_close else None,
            "realized_pnl": pos.realized_pnl.as_double() if pos.realized_pnl else 0.0,
            "unrealized_pnl": None,
            "currency": str(pos.realized_pnl.currency) if pos.realized_pnl else None,
            "entry_side": pos.entry.name,
            "peak_qty": str(pos.peak_qty),
            "is_open": pos.is_open,
            "event_count": pos.event_count,
            "ts_opened": ts_ns_to_iso(pos.ts_opened),
            "ts_closed": ts_ns_to_iso(pos.ts_closed) if pos.ts_closed and pos.ts_closed > 0 else None,
            "duration": str(pos.duration_ns) if pos.duration_ns else None,
            "duration_ns": pos.duration_ns if pos.duration_ns else None,
            "ts": ts_ns_to_iso(ts_event),
        }

    # --- Order events ---

    def _on_order_event(self, event: Event) -> None:
        if isinstance(event, OrderFilled):
            self._on_order_filled(event)
        elif isinstance(event, OrderAccepted):
            self._publish("orders", {
                "event": "order_accepted",
                "order_id": str(event.client_order_id),
                "instrument_id": str(event.instrument_id),
                "ts": str(event.ts_event),
            })
        elif isinstance(event, OrderRejected):
            self._publish("orders", {
                "event": "order_rejected",
                "order_id": str(event.client_order_id),
                "instrument_id": str(event.instrument_id),
                "reason": str(event.reason),
                "ts": str(event.ts_event),
            })
        elif isinstance(event, OrderCanceled):
            self._publish("orders", {
                "event": "order_canceled",
                "order_id": str(event.client_order_id),
                "instrument_id": str(event.instrument_id),
                "ts": str(event.ts_event),
            })
        elif isinstance(event, OrderExpired):
            self._publish("orders", {
                "event": "order_expired",
                "order_id": str(event.client_order_id),
                "instrument_id": str(event.instrument_id),
                "ts": str(event.ts_event),
            })

    def _on_order_filled(self, event: OrderFilled) -> None:
        payload = self._build_fill_payload(event)
        self._publish("fills", payload)

    def _build_fill_payload(self, event: OrderFilled) -> dict:
        strategy_id_str = str(event.strategy_id) if event.strategy_id else None
        return {
            "type": "fill.new",
            "id": 0,
            "node_type": self._node_type,
            "trade_id": str(event.trade_id),
            "position_id": str(event.position_id) if event.position_id else None,
            "client_order_id": str(event.client_order_id),
            "venue_order_id": str(event.venue_order_id) if event.venue_order_id else None,
            "strategy_id": strategy_id_str,
            "strategy_id_tag": strategy_id_str,
            "instrument_id": str(event.instrument_id),
            "order_side": event.order_side.name,
            "last_qty": str(event.last_qty),
            "last_px": str(event.last_px),
            "commission": str(event.commission.as_double()) if event.commission else None,
            "liquidity_side": str(event.liquidity_side.name) if event.liquidity_side else None,
            "ts_event": ts_ns_to_iso(event.ts_event),
            "ts": ts_ns_to_iso(event.ts_event),
        }

    # --- Bar events ---

    def _on_bar(self, bar: Bar) -> None:
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

    # --- Strategy snapshot + risk metrics ---

    def _on_strategy_snapshot(self, snapshot: Any) -> None:
        try:
            fields = json.loads(snapshot.fields_json)
        except Exception:
            return
        payload = {
            "type": "signal.snapshot",
            "node_type": self._node_type,
            "strategy_id": snapshot.strategy_id,
            "instrument_id": snapshot.instrument_id,
            "fields": fields,
            "ts": ts_ns_to_iso(snapshot.ts_event),
        }
        self._publish("signals", payload)
        if self._redis:
            try:
                key = f"tino:{self._node_type}:signals:history:{snapshot.strategy_id}"
                self._redis.lpush(key, json.dumps(payload, default=str))
                self._redis.ltrim(key, 0, 29)
            except Exception:
                pass

    def _on_risk_metrics(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        data["type"] = "risk.metrics"
        data["node_type"] = self._node_type
        self._publish("risk", data)
        if self._redis:
            try:
                self._redis.setex(
                    f"tino:{self._node_type}:risk_metrics", 30,
                    json.dumps(data, default=str),
                )
            except Exception:
                pass
