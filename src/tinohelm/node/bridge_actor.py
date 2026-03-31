"""BridgeActor — bridges NautilusTrader events to Redis PubSub and PostgreSQL."""
from __future__ import annotations

import collections
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    equity_snapshot_interval_secs: int = 60
    venue_name: str = "BINANCE"
    currency: str = "USDT"


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
        self._equity_interval = config.equity_snapshot_interval_secs
        self._venue_name_str = config.venue_name
        self._currency_str = config.currency
        self._venue = None  # resolved in on_start()
        self._currency_obj = None  # resolved in on_start()
        self._log_handler = None
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

        self._file_watcher_thread: threading.Thread | None = None
        self._registry: Any = None  # PortfolioRegistry, set by _common.py

        # LifecycleController — injected after node.build() via set_lifecycle_deps()
        self._lifecycle: Any = None  # LifecycleController | None
        self._lifecycle_trader: Any = None  # stored until on_start()
        self._lifecycle_risk_engine: Any = None  # stored until on_start()

    class _RedisLogHandler(logging.Handler):
        """Publishes log records to Redis with token bucket rate limiting."""

        def __init__(self, redis_client, node_type, rate_limit=10):
            super().__init__()
            self._redis = redis_client
            self._node_type = node_type
            self._tokens = float(rate_limit)
            self._last_refill = 0.0
            self._rate_limit = rate_limit

        def emit(self, record):
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
                    "ts": _ts_ns_to_iso(int(_time.time() * 1e9)),
                })
                self._redis.publish(f"tino:{self._node_type}:logs", payload)
            except Exception:
                pass

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
            # Inject registry reference into LifecycleController
            if hasattr(self, '_registry') and self._registry is not None:
                self._lifecycle._registry = self._registry
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

        # Resolve venue and currency objects
        from nautilus_trader.model.identifiers import Venue
        from tinohelm.data.instruments import _resolve_currency
        self._venue = Venue(self._venue_name_str)
        self._currency_obj = _resolve_currency(self._currency_str)

        # Equity snapshot timer
        self.clock.set_timer(
            name="equity_snapshot",
            interval=timedelta(seconds=self._equity_interval),
        )

        # Subscribe to risk metrics from RiskGuardActor
        self.msgbus.subscribe("risk.metrics.snapshot", self._on_risk_metrics)

        # Subscribe to strategy signal snapshots
        from tinohelm.data.strategy_snapshot import StrategySnapshot
        self.subscribe_data(StrategySnapshot)

        # Install log handler
        log_handler = self._RedisLogHandler(self._redis, self._node_type)
        log_handler.setLevel(logging.INFO)
        logging.getLogger("tinohelm").addHandler(log_handler)
        self._log_handler = log_handler

        # Command listener thread (runs Redis SUBSCRIBE in background)
        self._cmd_thread = threading.Thread(
            target=self._command_listener,
            daemon=True,
        )
        self._cmd_thread.start()

        # File watcher thread (polls strategies dir for changes)
        self._file_watcher_thread = threading.Thread(
            target=self._file_watcher,
            daemon=True,
        )
        self._file_watcher_thread.start()

        # Auto-resume portfolios that were running before last restart
        if hasattr(self, '_registry') and self._registry is not None:
            resume_names = [
                name for name, entry in self._registry._portfolios.items()
                if entry.was_running and entry.state == "available"
            ]
            if resume_names:
                self._pending_auto_resume = resume_names
                self.log.info(f"Will auto-resume {len(resume_names)} portfolio(s) in 15s: {resume_names}")
                self.clock.set_time_alert(
                    name="auto_resume_portfolios",
                    alert_time=self.clock.utc_now() + timedelta(seconds=15),
                )

        self.log.info(f"BridgeActor started for {self._node_type}")

    def on_event(self, event: Event) -> None:
        """Handle timer events for command dispatch (runs on NT event loop)."""
        if isinstance(event, TimeEvent) and event.name == "auto_resume_portfolios":
            self._do_auto_resume()
            return
        if isinstance(event, TimeEvent) and event.name == "bridge_cmd_dispatch":
            self._drain_pending_commands()
            # Check flatten-stop completion on the same timer
            if self._lifecycle and self._lifecycle._flatten_stop_pending:
                self._lifecycle.check_flatten_stop_completion()
        elif isinstance(event, TimeEvent) and event.name == "equity_snapshot":
            self._take_equity_snapshot()

    def on_data(self, data) -> None:
        """Handle structured data events (StrategySnapshot)."""
        from tinohelm.data.strategy_snapshot import StrategySnapshot
        if isinstance(data, StrategySnapshot):
            self._on_strategy_snapshot(data)

    def _do_auto_resume(self) -> None:
        """Auto-resume portfolios that were running before last restart."""
        names = getattr(self, '_pending_auto_resume', [])
        if not names or not self._lifecycle:
            return
        for name in names:
            try:
                self.log.info(f"Auto-resuming portfolio '{name}'")
                self._lifecycle.start_portfolio(name)
            except Exception as e:
                self.log.error(f"Auto-resume failed for '{name}': {e}")
        self._pending_auto_resume = []

    def _drain_pending_commands(self) -> None:
        """Process all queued commands on the NT event loop thread."""
        while self._pending_commands:
            cmd = self._pending_commands.popleft()
            action = cmd.get("cmd")
            strategy_id = cmd.get("strategy_id")

            # Internal command: rescan portfolio folders (enqueued by file watcher)
            if action == "_rescan_portfolios":
                if self._registry is not None:
                    try:
                        strategies_dir = Path(os.environ.get(
                            "TINO_STRATEGIES_DIR",
                            str(Path.home() / ".tino" / "strategies"),
                        ))
                        if strategies_dir.exists():
                            changed = self._registry.scan(strategies_dir)
                            if changed:
                                self.log.info(f"Portfolio folder change detected: {changed}")
                                self._publish("portfolio_update", {
                                    "portfolios": self._registry.get_all_states(),
                                })
                    except Exception as e:
                        self.log.error(f"Rescan portfolios error: {e}")
                continue  # Internal command, no ack needed

            if self._lifecycle is None:
                self.log.warning(f"Command '{action}' ignored: LifecycleController not initialized")
                self._publish("commands_ack", {"cmd": action, "status": "error", "reason": "no_lifecycle"})
                continue

            try:
                if action == "pause" and not strategy_id:
                    self._lifecycle.pause_all()
                    continue
                if action == "resume" and not strategy_id:
                    self._lifecycle.resume_all()
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
                elif action == "start_portfolio":
                    self._lifecycle.start_portfolio(cmd.get("portfolio_name", ""))
                elif action == "flatten_stop_portfolio":
                    self._lifecycle.flatten_stop_portfolio(cmd.get("portfolio_name", ""))
                elif action == "pause_portfolio":
                    self._lifecycle.pause_portfolio(cmd.get("portfolio_name", ""))
                elif action == "resume_portfolio":
                    self._lifecycle.resume_portfolio(cmd.get("portfolio_name", ""))
                elif action == "cancel_order":
                    coid = cmd.get("client_order_id")
                    if coid:
                        self._lifecycle.cancel_order(coid)
                    else:
                        self.log.warning("cancel_order: missing client_order_id")
                else:
                    self.log.warning(f"Unknown command: {action}")
            except Exception as e:
                self.log.error(f"Command '{action}' failed: {e}")
                self._publish("commands_ack", {"cmd": action, "status": "error", "reason": str(e)})

    def on_stop(self) -> None:
        if self._log_handler:
            logging.getLogger("tinohelm").removeHandler(self._log_handler)
            self._log_handler = None
        self._running = False
        if self._file_watcher_thread and self._file_watcher_thread.is_alive():
            self._file_watcher_thread.join(timeout=5)
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

    def _file_watcher(self) -> None:
        """Poll strategies directory for new/deleted portfolio folders."""
        import time
        while self._running:
            time.sleep(10)
            if self._registry is not None:
                # Enqueue rescan to run on the NT event loop thread (thread-safe)
                self._pending_commands.append({"cmd": "_rescan_portfolios"})

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
            # Use cache (actual trader state) rather than registry for live accuracy
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

            # Portfolio states
            if hasattr(self, '_registry') and self._registry is not None:
                payload["portfolios"] = self._registry.get_all_states()

            # Account balance for margin display
            try:
                if self._venue and self._currency_obj:
                    account = self.portfolio.account(self._venue)
                    if account:
                        bt = account.balance_total(self._currency_obj)
                        bf = account.balance_free(self._currency_obj)
                        payload["balance_total"] = float(bt.as_double()) if bt else 0.0
                        payload["balance_free"] = float(bf.as_double()) if bf else 0.0
            except Exception:
                pass

            # Subscribed bar types
            try:
                bar_types = self.cache.bar_types()
                payload["bar_types"] = [str(bt) for bt in bar_types] if bar_types else []
            except Exception:
                payload["bar_types"] = []

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

            # Persist portfolio registry state
            if hasattr(self, '_registry') and self._registry is not None:
                try:
                    self._redis.setex(
                        f"tino:{self._node_type}:portfolio_registry",
                        30,  # 2x heartbeat interval
                        json.dumps(self._registry.to_dict(), default=str),
                    )
                except Exception:
                    pass  # Best-effort persistence
        except Exception as e:
            self.log.error(f"Heartbeat error: {e}")

    def _take_equity_snapshot(self) -> None:
        """Sample current equity and publish + persist."""
        if not self._redis or self._venue is None:
            return
        try:
            account = self.portfolio.account(self._venue)
            if account is None:
                return
            balance_money = account.balance_total(self._currency_obj)
            if balance_money is None:
                return
            balance = float(balance_money.as_double())
            unrealized = 0.0
            try:
                pnls = self.portfolio.unrealized_pnls(self._venue)
                if pnls:
                    for _currency, pnl_money in pnls.items():
                        unrealized += float(pnl_money.as_double())
            except Exception:
                pass
            equity = balance + unrealized
            ts = _ts_ns_to_iso(self.clock.timestamp_ns())
            payload = {
                "type": "equity.snapshot",
                "node_type": self._node_type,
                "equity": round(equity, 2),
                "balance": round(balance, 2),
                "unrealized_pnl": round(unrealized, 2),
                "ts": ts,
            }
            # Publish to Redis PubSub
            self._publish("equity", payload)
            # Store in Redis list (capped)
            key = f"tino:{self._node_type}:equity_history"
            self._redis.rpush(key, json.dumps(payload, default=str))
            self._redis.ltrim(key, -1440, -1)
            # Persist to DB
            self._persist_equity_snapshot(equity, balance, unrealized, ts)
        except Exception as e:
            self.log.error(f"Equity snapshot error: {e}")

    def _persist_equity_snapshot(self, equity: float, balance: float, unrealized: float, ts: str) -> None:
        """Insert equity snapshot to DB."""
        if not self._db_engine:
            return
        try:
            from sqlalchemy import text
            from sqlalchemy.orm import Session
            stmt = text(
                "INSERT INTO equity_snapshots (node_type, equity, balance, unrealized_pnl, ts) "
                "VALUES (:node_type, :equity, :balance, :unrealized_pnl, :ts)"
            )
            with Session(self._db_engine) as session:
                session.execute(stmt, {
                    "node_type": self._node_type,
                    "equity": equity,
                    "balance": balance,
                    "unrealized_pnl": unrealized,
                    "ts": ts,
                })
                session.commit()
        except Exception as e:
            self.log.error(f"DB persist equity error: {e}")

    def _on_risk_metrics(self, data: dict) -> None:
        """Relay risk metrics from RiskGuardActor to Redis."""
        if not isinstance(data, dict):
            return
        data["type"] = "risk.metrics"
        data["node_type"] = self._node_type
        self._publish("risk", data)
        # Also persist as key for REST API initial load
        if self._redis:
            try:
                self._redis.setex(
                    f"tino:{self._node_type}:risk_metrics",
                    30,
                    json.dumps(data, default=str),
                )
            except Exception:
                pass

    def _on_strategy_snapshot(self, snapshot) -> None:
        """Relay strategy snapshot to Redis PubSub + ring buffer."""
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
            "ts": _ts_ns_to_iso(snapshot.ts_event),
        }
        # Publish to Redis PubSub for real-time WS
        self._publish("signals", payload)
        # Store in Redis ring buffer for sparkline history (30 entries)
        if self._redis:
            try:
                key = f"tino:{self._node_type}:signals:history:{snapshot.strategy_id}"
                self._redis.lpush(key, json.dumps(payload, default=str))
                self._redis.ltrim(key, 0, 29)
            except Exception as e:
                self.log.error(f"Redis signal history error: {e}")

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
        """Build a rich position payload for Redis publish.

        Field names match the REST PositionItem schema so the Rust TUI can
        deserialize with the same ``TradingPosition`` struct.  Legacy field
        names (``strategy_id``, ``duration_ns``, ``ts``) are kept for
        backward compatibility with older consumers.
        """
        strategy_id_str = str(pos.strategy_id) if pos.strategy_id else ""
        return {
            "type": "position.update",
            "event": event_type,
            "node_type": self._node_type,
            "id": 0,  # sentinel — DB auto-increment not available in WS
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
            "ts_opened": _ts_ns_to_iso(pos.ts_opened),
            "ts_closed": _ts_ns_to_iso(pos.ts_closed) if pos.ts_closed and pos.ts_closed > 0 else None,
            "duration": str(pos.duration_ns) if pos.duration_ns else None,
            "duration_ns": pos.duration_ns if pos.duration_ns else None,
            "ts": _ts_ns_to_iso(ts_event),
        }

    def _build_fill_payload(self, event: OrderFilled) -> dict:
        """Build a rich fill payload for Redis publish.

        Field names match the REST FillItem schema so the Rust TUI can
        deserialize with the same ``TradingFill`` struct.
        """
        strategy_id_str = str(event.strategy_id) if event.strategy_id else None
        return {
            "type": "fill.new",
            "id": 0,  # sentinel — DB auto-increment not available in WS
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
            "ts_event": _ts_ns_to_iso(event.ts_event),
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
