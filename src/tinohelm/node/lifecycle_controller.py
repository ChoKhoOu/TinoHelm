"""LifecycleController — manages strategy and system lifecycle commands.

Plain Python class (not Actor). Instantiated by BridgeActor after
``node.build()`` when trader and risk_engine references are available.

All public methods MUST be called on the NT event loop thread (via the
BridgeActor command-dispatch timer).
"""
from __future__ import annotations

import os
import signal as _signal
import time
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
        self._registry: Any = None  # PortfolioRegistry, set by BridgeActor
        self._flatten_stop_pending: dict[str, dict] = {}

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

    def pause_all(self) -> None:
        """Pause all registered strategies."""
        for strategy in self._trader.strategies():
            sid = str(strategy.id)
            self._msgbus.publish(f"{LIFECYCLE_PAUSE}.{sid}", "pause")
            self._paused_strategies.add(sid)
        self._log.warning("L1 Pause: all strategies")
        self._publish_ack(
            "commands_ack",
            {"cmd": "pause", "strategy_id": "all", "status": "ok"},
        )

    def resume_all(self) -> None:
        """Resume all paused strategies."""
        for sid in list(self._paused_strategies):
            self._msgbus.publish(f"{LIFECYCLE_RESUME}.{sid}", "resume")
        self._paused_strategies.clear()
        self._log.info("L1 Resume: all strategies")
        self._publish_ack(
            "commands_ack",
            {"cmd": "resume", "strategy_id": "all", "status": "ok"},
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
    # Portfolio lifecycle operations
    # ------------------------------------------------------------------

    def start_portfolio(self, name: str) -> None:
        """Load and start all strategies for a portfolio."""
        if self._registry is None:
            raise ValueError("PortfolioRegistry not initialized")

        entry = self._registry.get(name)
        if entry is None:
            raise ValueError(f"Portfolio '{name}' not found")
        if entry.state != "available":
            raise ValueError(f"Portfolio '{name}' is {entry.state}, not available")

        self._registry.mark_starting(name)

        try:
            from tinohelm.portfolio.config import load_portfolio_config
            from tinohelm.portfolio.loader import create_strategies, create_actors

            import os
            _strategies_dir = os.environ.get("TINO_STRATEGIES_DIR")
            portfolio_cfg = load_portfolio_config(name, strategies_dir=_strategies_dir)

            # Allocate tags with collision check
            existing_tags = {str(sid) for sid in self._trader.strategy_ids()}
            tags = self._registry.allocate_tags(name, len(portfolio_cfg.symbols), existing_tags)

            # Create instances
            strategy_instances = create_strategies(portfolio_cfg, order_id_tags=tags)
            lc_entry = self._registry.get(name)
            actor_instances = create_actors(
                portfolio_cfg,
                portfolio_name=name,
                strategy_tag_prefix=lc_entry.order_id_tag_prefix if lc_entry else None,
            )

            # Atomic registration: rollback on partial failure
            added_strategies = []
            added_actors = []
            try:
                for strategy in strategy_instances:
                    if strategy.id in self._trader.strategy_ids():
                        raise RuntimeError(f"Strategy ID collision: {strategy.id}")
                    self._trader.add_strategy(strategy)
                    added_strategies.append(strategy)

                for actor in actor_instances:
                    self._trader.add_actor(actor)
                    added_actors.append(actor)

                for strategy in strategy_instances:
                    self._trader.start_strategy(strategy.id)
            except Exception:
                for a in added_actors:
                    try:
                        self._trader.remove_actor(a.id)
                    except Exception:
                        pass
                for s in added_strategies:
                    try:
                        self._trader.remove_strategy(s.id)
                    except Exception:
                        pass
                raise

            strategy_ids = [str(s.id) for s in strategy_instances]
            self._registry.mark_running(name, strategy_ids)

            self._log.info(f"Started portfolio '{name}' with {len(strategy_ids)} strategies")
            self._publish_ack("commands_ack", {
                "cmd": "start_portfolio", "name": name,
                "status": "ok", "strategy_ids": strategy_ids,
            })
        except Exception as e:
            self._registry.mark_stopped(name)
            self._log.error(f"Failed to start portfolio '{name}': {e}")
            self._publish_ack("commands_ack", {
                "cmd": "start_portfolio", "name": name,
                "status": "error", "reason": str(e),
            })

    def flatten_stop_portfolio(self, name: str) -> None:
        """Flatten all positions then stop all strategies for a portfolio."""
        if self._registry is None:
            raise ValueError("PortfolioRegistry not initialized")

        entry = self._registry.get(name)
        if entry is None:
            raise ValueError(f"Portfolio '{name}' not found")
        if entry.state not in ("running", "paused"):
            raise ValueError(f"Portfolio '{name}' is {entry.state}, cannot flatten-stop")

        self._registry.mark_flattening(name)

        # Resume any paused strategies before flattening so market_exit works
        for sid_str in entry.strategy_ids:
            if sid_str in self._paused_strategies:
                self._resume_strategy(sid_str)

        for sid_str in entry.strategy_ids:
            try:
                sid = self._resolve_strategy_id(sid_str)
                strategy = self._trader.strategy(sid)
                if strategy:
                    strategy.market_exit()
            except Exception as e:
                self._log.error(f"market_exit failed for {sid_str}: {e}")

        self._flatten_stop_pending[name] = {
            "start_ts": time.time(),
            "strategy_ids": list(entry.strategy_ids),
        }

        self._publish_ack("commands_ack", {
            "cmd": "flatten_stop_portfolio", "name": name,
            "status": "flattening",
        })

    def check_flatten_stop_completion(self) -> None:
        """Check if pending flatten-stops are complete. Called from BridgeActor timer."""
        from nautilus_trader.model.identifiers import StrategyId

        for name, info in list(self._flatten_stop_pending.items()):
            elapsed = time.time() - info["start_ts"]
            all_flat = True

            for sid_str in info["strategy_ids"]:
                try:
                    sid = StrategyId(sid_str)
                    positions = self._trader.cache.positions_open(strategy_id=sid)
                    if positions:
                        all_flat = False
                        break
                except Exception:
                    all_flat = False
                    break

            if all_flat:
                for sid_str in info["strategy_ids"]:
                    try:
                        self._trader.remove_strategy(StrategyId(sid_str))
                    except Exception as e:
                        self._log.error(f"remove_strategy failed for {sid_str}: {e}")
                # Clean up paused strategies set
                for sid_str in info["strategy_ids"]:
                    self._paused_strategies.discard(sid_str)
                self._registry.mark_stopped(name)
                del self._flatten_stop_pending[name]
                self._publish_ack("commands_ack", {
                    "cmd": "flatten_stop_portfolio", "name": name,
                    "status": "ok",
                })
                self._log.info(f"Portfolio '{name}' fully stopped")
            elif elapsed > 60:
                # Timeout -- still remove strategies to prevent orphans
                for sid_str in info["strategy_ids"]:
                    try:
                        self._trader.remove_strategy(StrategyId(sid_str))
                    except Exception as e:
                        self._log.error(f"Failed to remove strategy {sid_str} on timeout: {e}")
                # Clean up paused strategies set
                for sid_str in info["strategy_ids"]:
                    self._paused_strategies.discard(sid_str)
                self._log.critical(
                    f"Portfolio '{name}' flatten-stop timed out after 60s. "
                    f"Positions still open. Manual intervention required."
                )
                self._registry.mark_stopped(name)
                del self._flatten_stop_pending[name]
                self._publish_ack("commands_ack", {
                    "cmd": "flatten_stop_portfolio", "name": name,
                    "status": "timeout",
                    "reason": "Positions not flat after 60s. Manual force-stop required.",
                })

    def pause_portfolio(self, name: str) -> None:
        """Pause all strategies in a portfolio via soft pause."""
        if self._registry is None:
            raise ValueError("PortfolioRegistry not initialized")

        entry = self._registry.get(name)
        if entry is None or entry.state != "running":
            raise ValueError(
                f"Cannot pause portfolio '{name}' "
                f"(state: {entry.state if entry else 'not found'})"
            )
        for sid_str in entry.strategy_ids:
            self.pause_strategy(sid_str)
        self._registry.mark_paused(name)
        self._publish_ack("commands_ack", {
            "cmd": "pause_portfolio", "name": name, "status": "ok",
        })

    def resume_portfolio(self, name: str) -> None:
        """Resume all strategies in a paused portfolio."""
        if self._registry is None:
            raise ValueError("PortfolioRegistry not initialized")

        entry = self._registry.get(name)
        if entry is None or entry.state != "paused":
            raise ValueError(
                f"Cannot resume portfolio '{name}' "
                f"(state: {entry.state if entry else 'not found'})"
            )
        for sid_str in entry.strategy_ids:
            self.resume_strategy(sid_str)
        self._registry.mark_running(name, entry.strategy_ids)
        self._publish_ack("commands_ack", {
            "cmd": "resume_portfolio", "name": name, "status": "ok",
        })

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

        state_dict = {
            "trading_state": trading_state,
            "paused": sorted(self._paused_strategies),
            "strategy_states": strategy_states,
        }

        # Portfolio states
        if self._registry is not None:
            state_dict["portfolios"] = self._registry.get_all_states()

        return state_dict

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def cancel_order(self, client_order_id: str) -> None:
        """Cancel a specific order by client_order_id.

        Resolves the owning strategy from the order and delegates cancellation.
        NT Trader does not expose cancel_order() directly — must go through strategy.
        """
        from nautilus_trader.model.identifiers import ClientOrderId

        coid = ClientOrderId(client_order_id)
        order = self._trader.cache.order(coid)
        if order is None:
            self._log.warning(f"Order {client_order_id} not found in cache")
            if self._publish_ack:
                self._publish_ack("commands_ack", {"cmd": "cancel_order", "client_order_id": client_order_id, "status": "not_found"})
            return
        if order.is_closed:
            self._log.info(f"Order {client_order_id} already closed ({order.status})")
            if self._publish_ack:
                self._publish_ack("commands_ack", {"cmd": "cancel_order", "client_order_id": client_order_id, "status": "already_closed"})
            return
        # Resolve owning strategy
        strategy = self._trader.strategy(order.strategy_id)
        if strategy is None:
            self._log.warning(f"Strategy {order.strategy_id} not found for order {client_order_id}")
            if self._publish_ack:
                self._publish_ack("commands_ack", {"cmd": "cancel_order", "client_order_id": client_order_id, "status": "strategy_not_found"})
            return
        strategy.cancel_order(order)
        self._log.info(f"Cancel submitted for order {client_order_id} via strategy {order.strategy_id}")
        if self._publish_ack:
            self._publish_ack("commands_ack", {"cmd": "cancel_order", "client_order_id": client_order_id, "status": "submitted"})

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

    def _resume_strategy(self, sid_str: str) -> None:
        """Internal resume — no ack published. Used before flatten-stop."""
        try:
            sid = self._resolve_strategy_id(sid_str)
            self._msgbus.publish(f"{LIFECYCLE_RESUME}.{sid}", "resume")
            self._paused_strategies.discard(str(sid))
            self._log.info(f"L1 Resume (internal): strategy {sid}")
        except Exception as e:
            self._log.error(f"Failed to resume strategy {sid_str}: {e}")

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
