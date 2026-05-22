"""SnapshotActor — bridges NT events to Redis PubSub for external consumers.

Subscribes to position, order, bar, strategy snapshot, and risk metric events,
builds JSON payloads via :mod:`tinohelm.node.actors.serialize`, and publishes
to Redis PubSub + ring buffers. Also includes a rate-limited
:class:`_RedisLogHandler` built on :class:`TokenBucket` for log forwarding.
"""
from __future__ import annotations

import json
import logging
import time as _time
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
from tinohelm.node.actors.rate_limit import TokenBucket
from tinohelm.node.actors.serialize import (
    build_bar_event,
    build_fill_event,
    build_order_lifecycle_event,
    build_position_update,
    build_strategy_signal_snapshot,
    tag_risk_metrics,
)


class SnapshotActorConfig(ActorConfig):
    redis_url: str = "redis://localhost:6379"
    node_type: str = "sandbox"


_ORDER_EVENT_KINDS = (
    (OrderAccepted, "order_accepted"),
    (OrderRejected, "order_rejected"),
    (OrderCanceled, "order_canceled"),
    (OrderExpired, "order_expired"),
)


class _RedisLogHandler(logging.Handler):
    """Publishes log records to Redis with token-bucket rate limiting."""

    def __init__(self, redis_client: redis.Redis, node_type: str, rate_limit: int = 10):
        super().__init__()
        self._redis = redis_client
        self._node_type = node_type
        self._bucket = TokenBucket(rate_limit)

    def emit(self, record: logging.LogRecord) -> None:
        if not self._bucket.try_consume():
            return
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
        if not isinstance(event, (PositionOpened, PositionChanged, PositionClosed)):
            return
        payload = build_position_update(
            event.position, self._node_type, type(event).__name__, event.ts_event,
        )
        self._publish("positions", payload)

    # --- Order events ---

    def _on_order_event(self, event: Event) -> None:
        if isinstance(event, OrderFilled):
            self._publish("fills", build_fill_event(event, self._node_type))
            return
        for event_cls, kind in _ORDER_EVENT_KINDS:
            if isinstance(event, event_cls):
                self._publish("orders", build_order_lifecycle_event(event, kind))
                return

    # --- Bar events ---

    def _on_bar(self, bar: Bar) -> None:
        self._publish("bars", build_bar_event(bar))

    # --- Strategy snapshot + risk metrics ---

    def _on_strategy_snapshot(self, snapshot: Any) -> None:
        try:
            fields = json.loads(snapshot.fields_json)
        except Exception:
            return
        payload = build_strategy_signal_snapshot(snapshot, self._node_type, fields)
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
        payload = tag_risk_metrics(data, self._node_type)
        self._publish("risk", payload)
        if self._redis:
            try:
                self._redis.setex(
                    f"tino:{self._node_type}:risk_metrics", 30,
                    json.dumps(payload, default=str),
                )
            except Exception:
                pass
