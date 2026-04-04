"""HealthActor — heartbeat, file watching, auto-resume, registry snapshots."""
from __future__ import annotations

import json
import os
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import redis

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import ActorConfig
from nautilus_trader.core.message import Event


class HealthActorConfig(ActorConfig):
    redis_url: str = "redis://localhost:6379"
    node_type: str = "sandbox"
    heartbeat_interval_secs: int = 5
    heartbeat_ttl_secs: int = 15
    venue_name: str = "BINANCE"
    currency: str = "USDT"


class HealthActor(Actor):
    """Heartbeat, strategy dir file watch, auto-resume, and registry snapshots."""

    def __init__(self, config: HealthActorConfig) -> None:
        super().__init__(config)
        self._redis_url = config.redis_url
        self._node_type = config.node_type
        self._hb_interval = config.heartbeat_interval_secs
        self._hb_ttl = config.heartbeat_ttl_secs
        self._venue_name_str = config.venue_name
        self._currency_str = config.currency
        self._redis: redis.Redis | None = None
        self._venue = None
        self._currency_obj = None
        self._registry: Any = None
        self._lifecycle: Any = None  # CommandActor's lifecycle ref, for state queries
        self._file_watcher_thread: threading.Thread | None = None
        self._running = False
        # Shared deque with CommandActor for file-watcher rescan commands
        self._command_deque: Any = None

    def on_start(self) -> None:
        self._redis = redis.from_url(self._redis_url)
        self._running = True

        from nautilus_trader.model.identifiers import Venue
        from tinohelm.data.instruments import _resolve_currency
        self._venue = Venue(self._venue_name_str)
        self._currency_obj = _resolve_currency(self._currency_str)

        # Heartbeat timer
        self.clock.set_timer(
            name="health_heartbeat",
            interval=timedelta(seconds=self._hb_interval),
            callback=self._send_heartbeat,
        )

        # File watcher thread
        self._file_watcher_thread = threading.Thread(target=self._file_watcher, daemon=True)
        self._file_watcher_thread.start()

        # Auto-resume
        if self._registry is not None:
            resume_names = [
                name for name, entry in self._registry._strategies.items()
                if entry.was_running and entry.state == "available"
            ]
            if resume_names:
                self._pending_auto_resume = resume_names
                self.log.info(f"Will auto-resume {len(resume_names)} strategy(s) in 15s: {resume_names}")
                self.clock.set_time_alert(
                    name="auto_resume_strategies",
                    alert_time=self.clock.utc_now() + timedelta(seconds=15),
                )

        self.log.info(f"HealthActor started for {self._node_type}")

    def on_event(self, event: Event) -> None:
        if isinstance(event, TimeEvent) and event.name == "auto_resume_strategies":
            self._do_auto_resume()

    def on_stop(self) -> None:
        self._running = False
        if self._file_watcher_thread and self._file_watcher_thread.is_alive():
            self._file_watcher_thread.join(timeout=5)
        if self._redis:
            self._redis.delete(f"tino:heartbeat:{self._node_type}")
            self._redis.close()

    def _do_auto_resume(self) -> None:
        names = getattr(self, '_pending_auto_resume', [])
        if not names:
            return
        # Use CommandActor's lifecycle via the shared deque
        for name in names:
            if self._command_deque is not None:
                self._command_deque.append({"cmd": "start_strategy", "strategy_name": name})
                self.log.info(f"Queued auto-resume for '{name}'")
            else:
                self.log.warning(f"Cannot auto-resume '{name}': no command deque")
        self._pending_auto_resume = []

    def _file_watcher(self) -> None:
        """Poll strategies directory for changes."""
        import time
        while self._running:
            time.sleep(10)
            if self._command_deque is not None:
                self._command_deque.append({"cmd": "_rescan_strategies"})

    def _send_heartbeat(self, event: Any) -> None:
        if not self._redis:
            return
        try:
            strategies = self.cache.strategy_ids()
            positions = self.cache.positions()

            payload: dict[str, Any] = {
                "ts": str(self.clock.utc_now()),
                "node_type": self._node_type,
                "strategies": len(strategies) if strategies else 0,
                "positions": len(positions) if positions else 0,
            }

            # Lifecycle state
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

            # Registry strategies
            if self._registry is not None:
                payload["strategies"] = self._registry.get_all_states()

            # Account balance
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

            # Bar types
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

            # Lifecycle state key
            self._redis.setex(
                f"tino:{self._node_type}:lifecycle_state",
                self._hb_ttl,
                json.dumps({
                    "trading_state": payload["trading_state"],
                    "strategy_states": payload["strategy_states"],
                    "paused": lc_state.get("paused", []),
                }, default=str),
            )

            # Strategy registry snapshot
            if self._registry is not None:
                try:
                    self._redis.setex(
                        f"tino:{self._node_type}:strategy_registry",
                        30,
                        json.dumps(self._registry.to_dict(), default=str),
                    )
                except Exception:
                    pass
        except Exception as e:
            self.log.error(f"Heartbeat error: {e}")
