"""CommandActor — bridges external Redis commands to NT lifecycle operations.

Uses a daemon thread with Redis SUBSCRIBE + deque for zero-latency command
delivery, drained by an NT timer on the event loop thread.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import redis

from nautilus_trader.common.actor import Actor
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import ActorConfig

from tinohelm.node.actors._utils import redis_publish

logger = logging.getLogger(__name__)


class CommandActorConfig(ActorConfig):
    redis_url: str = "redis://localhost:6379"
    node_type: str = "sandbox"
    drain_interval_ms: int = 200


class CommandActor(Actor):
    """Listens for external commands via Redis SUBSCRIBE, dispatches on NT event loop."""

    def __init__(self, config: CommandActorConfig) -> None:
        super().__init__(config)
        self._redis_url = config.redis_url
        self._node_type = config.node_type
        self._drain_interval_ms = config.drain_interval_ms
        self._redis: redis.Redis | None = None
        self._cmd_thread: threading.Thread | None = None
        self._cmd_redis: redis.Redis | None = None
        self._cmd_pubsub: Any = None
        self._running = False
        self._pending_commands: collections.deque = collections.deque()

        # Lifecycle deps — injected after node.build()
        self._lifecycle: Any = None
        self._lifecycle_trader: Any = None
        self._lifecycle_risk_engine: Any = None
        self._registry: Any = None

    def set_lifecycle_deps(self, trader: Any, risk_engine: Any) -> None:
        """Store trader and risk_engine for LifecycleController init."""
        self._lifecycle_trader = trader
        self._lifecycle_risk_engine = risk_engine

    def on_start(self) -> None:
        self._redis = redis.from_url(self._redis_url)
        self._running = True

        # Initialize LifecycleController
        if self._lifecycle_trader is not None and self._lifecycle_risk_engine is not None:
            from tinohelm.node.lifecycle_controller import LifecycleController
            self._lifecycle = LifecycleController(
                trader=self._lifecycle_trader,
                risk_engine=self._lifecycle_risk_engine,
                msgbus=self.msgbus,
                log=self.log,
                publish_ack=lambda suffix, data: redis_publish(
                    self._redis, self._node_type, suffix, data
                ),
            )
            if self._registry is not None:
                self._lifecycle._registry = self._registry
            self.log.info("LifecycleController initialized in CommandActor")
        else:
            self.log.warning("LifecycleController NOT initialized (no deps)")

        # Command dispatch timer
        self.clock.set_timer(
            name="cmd_dispatch",
            interval=timedelta(milliseconds=self._drain_interval_ms),
        )

        # Daemon thread for Redis SUBSCRIBE
        self._cmd_thread = threading.Thread(target=self._command_listener, daemon=True)
        self._cmd_thread.start()

        self.log.info(f"CommandActor started for {self._node_type}")

    def on_event(self, event: Any) -> None:
        if isinstance(event, TimeEvent) and event.name == "cmd_dispatch":
            self._drain_pending_commands()
            if self._lifecycle and self._lifecycle._flatten_stop_pending:
                self._lifecycle.check_flatten_stop_completion()

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
            self._redis.close()

    def _command_listener(self) -> None:
        """Background thread: Redis SUBSCRIBE for instant command delivery."""
        try:
            r = redis.Redis.from_url(self._redis_url, decode_responses=True)
            ps = r.pubsub()
            self._cmd_redis = r
            self._cmd_pubsub = ps
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
                    self.log.info(f"Received command: {cmd.get('cmd')}")
                    self._pending_commands.append(cmd)
                except Exception as e:
                    self.log.error(f"Command parse error: {e}")

            ps.unsubscribe()
            ps.close()
            r.close()
        except Exception as e:
            self.log.error(f"Command listener error: {e}")

    def _drain_pending_commands(self) -> None:
        """Process all queued commands on the NT event loop thread."""
        while self._pending_commands:
            cmd = self._pending_commands.popleft()
            action = cmd.get("cmd")
            strategy_id = cmd.get("strategy_id")

            # Internal: rescan strategy folders (from HealthActor file watcher)
            if action == "_rescan_strategies":
                if self._registry is not None:
                    try:
                        strategies_dir = Path(os.environ.get(
                            "TINO_STRATEGIES_DIR",
                            str(Path.home() / ".tino" / "strategies"),
                        ))
                        if strategies_dir.exists():
                            changed = self._registry.scan(strategies_dir)
                            if changed:
                                self.log.info(f"Strategy folder change detected: {changed}")
                                redis_publish(self._redis, self._node_type, "strategy_update", {
                                    "strategies": self._registry.get_all_states(),
                                })
                    except Exception as e:
                        self.log.error(f"Rescan strategies error: {e}")
                continue

            if self._lifecycle is None:
                self.log.warning(f"Command '{action}' ignored: no LifecycleController")
                redis_publish(self._redis, self._node_type, "commands_ack", {
                    "cmd": action, "status": "error", "reason": "no_lifecycle",
                })
                continue

            try:
                if action == "pause" and not strategy_id:
                    self._lifecycle.pause_all()
                elif action == "resume" and not strategy_id:
                    self._lifecycle.resume_all()
                elif action == "pause":
                    self._lifecycle.pause_strategy_id(strategy_id)
                elif action == "resume":
                    self._lifecycle.resume_strategy_id(strategy_id)
                elif action == "flatten":
                    self._lifecycle.flatten(strategy_id)
                elif action == "halt":
                    self._lifecycle.halt()
                elif action == "unhalt":
                    self._lifecycle.unhalt()
                elif action == "shutdown":
                    self._lifecycle.shutdown()
                elif action == "start_strategy":
                    name = cmd.get("strategy_name", "")
                    self._lifecycle.start_strategy(name)
                elif action == "flatten_stop_strategy":
                    name = cmd.get("strategy_name", "")
                    self._lifecycle.flatten_stop_strategy(name)
                elif action == "pause_strategy":
                    name = cmd.get("strategy_name", "")
                    self._lifecycle.pause_strategy(name)
                elif action == "resume_strategy":
                    name = cmd.get("strategy_name", "")
                    self._lifecycle.resume_strategy(name)
                elif action == "cancel_order":
                    coid = cmd.get("client_order_id")
                    if coid:
                        self._lifecycle.cancel_order(coid)
                else:
                    self.log.warning(f"Unknown command: {action}")
            except Exception as e:
                self.log.error(f"Command '{action}' failed: {e}")
                redis_publish(self._redis, self._node_type, "commands_ack", {
                    "cmd": action, "status": "error", "reason": str(e),
                })
