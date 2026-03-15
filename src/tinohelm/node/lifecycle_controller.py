"""LifecycleController — manages strategy and system lifecycle commands.

Plain Python class (not Actor). Instantiated by BridgeActor after
``node.build()`` when trader and risk_engine references are available.

All public methods MUST be called on the NT event loop thread (via the
BridgeActor command-dispatch timer).
"""
from __future__ import annotations

import os
import signal as _signal
from typing import Any

from tinohelm.node.topics import (
    LIFECYCLE_FLATTEN,
    LIFECYCLE_PAUSE,
    LIFECYCLE_RESUME,
    RISK_GUARD_STATE,
)


class LifecycleController:
    """Manages strategy and system lifecycle commands.

    Levels:
        L1 — Soft Pause: msgbus ``lifecycle.pause.{strategy_id}``
        L2 — Flatten: ``trader.market_exit_strategy(strategy_id)``
        L3 — Halt: ``risk_engine.set_trading_state(TradingState.HALTED)``
        L4 — Shutdown: ``os.kill(SIGTERM)``
    """

    def __init__(
        self,
        trader: Any,
        risk_engine: Any,
        msgbus: Any,
        log: Any,
        publish_ack: Any,
    ) -> None:
        self._trader = trader
        self._risk_engine = risk_engine
        self._msgbus = msgbus
        self._log = log
        self._publish_ack = publish_ack  # callable(channel_suffix, data)
        self._paused_strategies: set[str] = set()

        # Subscribe to RiskGuard breach actions for enforcement
        self._msgbus.subscribe(RISK_GUARD_STATE, self._on_risk_guard_breach)

    # ------------------------------------------------------------------
    # L1 — Soft Pause / Resume
    # ------------------------------------------------------------------

    def pause_strategy(self, strategy_id: str) -> None:
        """Pause a specific strategy via L1 soft pause."""
        sid = self._resolve_strategy_id(strategy_id)
        self._msgbus.publish(f"{LIFECYCLE_PAUSE}.{sid}", "pause")
        self._paused_strategies.add(str(sid))
        self._log.warning(f"L1 Pause: strategy {sid}")
        self._publish_ack(
            "commands_ack",
            {"cmd": "pause", "strategy_id": str(sid), "status": "ok"},
        )

    def resume_strategy(self, strategy_id: str) -> None:
        """Resume a paused strategy."""
        sid = self._resolve_strategy_id(strategy_id)
        self._msgbus.publish(f"{LIFECYCLE_RESUME}.{sid}", "resume")
        self._paused_strategies.discard(str(sid))
        self._log.info(f"L1 Resume: strategy {sid}")
        self._publish_ack(
            "commands_ack",
            {"cmd": "resume", "strategy_id": str(sid), "status": "ok"},
        )

    # ------------------------------------------------------------------
    # L2 — Flatten
    # ------------------------------------------------------------------

    def flatten(self, strategy_id: str | None = None) -> None:
        """Flatten positions for one or all strategies."""
        if strategy_id:
            sid = self._resolve_strategy_id(strategy_id)
            self._trader.market_exit_strategy(sid)
            self._log.warning(f"L2 Flatten: strategy {sid}")
            self._publish_ack(
                "commands_ack",
                {"cmd": "flatten", "strategy_id": str(sid), "status": "ok"},
            )
        else:
            for strategy in self._trader.strategies():
                strategy.market_exit()
            self._log.warning("L2 Flatten: all strategies")
            self._publish_ack(
                "commands_ack",
                {"cmd": "flatten", "strategy_id": "all", "status": "ok"},
            )

    # ------------------------------------------------------------------
    # L3 — Halt / Unhalt (RiskEngine TradingState)
    # ------------------------------------------------------------------

    def halt(self, *, _publish_ack: bool = True) -> None:
        """Set system-wide TradingState to HALTED — blocks all new orders."""
        try:
            from nautilus_trader.model.enums import TradingState

            self._risk_engine.set_trading_state(TradingState.HALTED)
            self._log.warning("L3 Halt: TradingState set to HALTED")
        except Exception as e:
            self._log.error(f"L3 Halt via RiskEngine failed: {e}")
            # Fallback: publish advisory halt via msgbus
            self._msgbus.publish(LIFECYCLE_FLATTEN, "halt_fallback")
            self._log.warning("L3 Halt: published fallback via msgbus")
            if _publish_ack:
                self._publish_ack("commands_ack", {"cmd": "halt", "status": "fallback"})
            return
        if _publish_ack:
            self._publish_ack("commands_ack", {"cmd": "halt", "status": "ok"})

    def unhalt(self) -> None:
        """Restore TradingState to ACTIVE — allows new orders."""
        try:
            from nautilus_trader.model.enums import TradingState

            self._risk_engine.set_trading_state(TradingState.ACTIVE)
            self._log.info("L3 Unhalt: TradingState set to ACTIVE")
        except Exception as e:
            self._log.error(f"L3 Unhalt failed: {e}")
            self._publish_ack("commands_ack", {"cmd": "unhalt", "status": "error", "reason": str(e)})
            return
        self._publish_ack("commands_ack", {"cmd": "unhalt", "status": "ok"})

    # ------------------------------------------------------------------
    # L4 — Shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Full node shutdown via SIGTERM."""
        self._log.warning("L4 Shutdown: sending SIGTERM")
        self._publish_ack(
            "commands_ack", {"cmd": "shutdown", "status": "received"}
        )
        os.kill(os.getpid(), _signal.SIGTERM)

    # ------------------------------------------------------------------
    # State query
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        """Return current lifecycle state for heartbeat / API."""
        trading_state = "active"
        try:
            ts = self._risk_engine.trading_state
            trading_state = ts.name.lower()
        except Exception:
            pass

        strategy_states: dict[str, str] = {}
        try:
            for sid in self._trader.strategy_ids():
                sid_str = str(sid)
                if sid_str in self._paused_strategies:
                    strategy_states[sid_str] = "paused"
                else:
                    strategy_states[sid_str] = "running"
        except Exception as e:
            self._log.warning(f"Could not enumerate strategy states: {e}")

        return {
            "trading_state": trading_state,
            "paused": sorted(self._paused_strategies),
            "strategy_states": strategy_states,
        }

    # ------------------------------------------------------------------
    # RiskGuard integration (Q2)
    # ------------------------------------------------------------------

    def _on_risk_guard_breach(self, action: str) -> None:
        """Enforce RiskGuard breach actions via system-level controls."""
        if action == "halt_new":
            self._log.warning(
                "RiskGuard breach: halt_new -> enforcing TradingState.HALTED"
            )
            self.halt(_publish_ack=False)
        elif action == "reduce_only":
            self._log.warning(
                "RiskGuard breach: reduce_only -> enforcing TradingState.REDUCING"
            )
            try:
                from nautilus_trader.model.enums import TradingState

                self._risk_engine.set_trading_state(TradingState.REDUCING)
            except Exception as e:
                self._log.error(f"Failed to set REDUCING state: {e}")
        # flatten_all is already handled by RiskGuard directly

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_strategy_id(self, strategy_id_str: str) -> Any:
        """Validate and convert a string to a StrategyId.

        Raises ValueError with available IDs on mismatch.
        """
        from nautilus_trader.model.identifiers import StrategyId

        try:
            target = StrategyId(strategy_id_str)
        except Exception:
            available = [str(sid) for sid in self._trader.strategy_ids()]
            raise ValueError(
                f"Invalid strategy_id format: '{strategy_id_str}'. "
                f"Expected format: 'ClassName-tag'. Available: {available}"
            )

        known_ids = self._trader.strategy_ids()
        if target not in known_ids:
            available = [str(sid) for sid in known_ids]
            raise ValueError(
                f"Strategy '{strategy_id_str}' not found. "
                f"Available strategies: {available}"
            )
        return target
