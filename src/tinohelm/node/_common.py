"""Shared helpers for sandbox and live node entry-points."""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
from typing import Any

import redis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Entry-point helpers (used by live_main.py / sandbox_main.py)
# ---------------------------------------------------------------------------

def load_node_config(
    node_type: str,
    settings: Any,
    logger_: logging.Logger,
) -> dict[str, Any]:
    """Read node config from Redis, falling back to factory-built config.

    Returns the config dict (credentials NOT included — call
    :func:`inject_credentials` afterwards).
    """
    from tinohelm.node.factory import build_trading_node_config

    r = redis.Redis.from_url(settings.redis.url, decode_responses=True)
    try:
        config_raw = r.get(f"tino:node:config:{node_type}")
        if config_raw:
            config = json.loads(config_raw)
            logger_.info(
                "Loaded %s config from Redis (version=%s)",
                node_type, config.get("config_version", "unknown"),
            )
        else:
            config = build_trading_node_config(node_type, [], settings)
            logger_.info("Built %s config from settings (no Redis config found)", node_type)
    finally:
        r.close()
    return config


def inject_credentials(
    config: dict[str, Any],
    settings: Any,
) -> None:
    """Inject Binance credentials from env vars into *config* (in-place).

    Removes the ``from_env`` sentinel written by the factory.
    """
    api_key = os.environ.get("BINANCE_API_KEY", "")
    api_secret = os.environ.get("BINANCE_API_SECRET", "")

    config.setdefault("binance", {})
    config["binance"]["api_key"] = api_key
    config["binance"]["api_secret"] = api_secret
    config["binance"].setdefault("account_type", settings.binance.account_type)
    config["binance"].pop("from_env", None)


def inject_lifecycle_deps(node: Any, bridge_actor: Any) -> None:
    """Inject trader and risk_engine references into BridgeActor.

    MUST be called AFTER ``node.build()`` so that ``node.kernel`` is ready.
    """
    try:
        bridge_actor.set_lifecycle_deps(
            trader=node.trader,
            risk_engine=node.kernel.risk_engine,
        )
        logger.info("Lifecycle deps injected into BridgeActor")
    except Exception as e:
        logger.error("Failed to inject lifecycle deps: %s", e)


def load_components(
    node: Any,
    config: dict[str, Any],
) -> Any:
    """Load strategies, actors, and BridgeActor onto a TradingNode.

    Handles both portfolio-based and legacy strategy-path loading.

    Returns the BridgeActor instance for lifecycle dependency injection.
    """
    from nautilus_trader.config import ImportableStrategyConfig

    from tinohelm.node.bridge_actor import BridgeActor, BridgeActorConfig
    from tinohelm.portfolio.config import load_portfolio_config
    from tinohelm.portfolio.loader import create_actors, create_strategies

    node_type = config["node_type"]

    # ---- Strategies and actors via portfolio_loader -----------------------
    portfolio_config_name = config.get("portfolio_config")
    if portfolio_config_name:
        portfolio_cfg = load_portfolio_config(portfolio_config_name)
        strategy_instances = create_strategies(portfolio_cfg)
        actor_instances = create_actors(portfolio_cfg)
    else:
        # Legacy: load from strategy paths list
        strategy_instances = []
        for strat_path in config.get("strategies", []):
            if ":" not in strat_path:
                logger.error("Invalid strategy path format (expected 'module:Class'): %s", strat_path)
                continue
            module_path, class_name = strat_path.rsplit(":", 1)
            if ".." in module_path or module_path.startswith("/"):
                logger.error("Suspicious strategy module path rejected: %s", module_path)
                continue
            strategy_instances.append(
                ImportableStrategyConfig(
                    strategy_path=module_path,
                    config_path=f"{module_path}:{class_name}Config",
                ).create()
            )
        actor_instances = []

    for strategy in strategy_instances:
        node.trader.add_strategy(strategy)
    logger.info("Added %d strategy instance(s)", len(strategy_instances))

    for actor in actor_instances:
        node.trader.add_actor(actor)
    if actor_instances:
        logger.info("Added %d actor(s)", len(actor_instances))

    # ---- BridgeActor for Redis event bridging and commands ----------------
    redis_url = config["redis_url"]
    redis_db = config.get("redis_db", 0)
    bridge_config = BridgeActorConfig(
        redis_url=f"{redis_url}/{redis_db}" if "/" not in redis_url.split("//")[-1] else redis_url,
        node_type=node_type,
        db_url=config.get("db_url", ""),
    )
    bridge_actor = BridgeActor(config=bridge_config)
    node.trader.add_actor(bridge_actor)

    return bridge_actor


def run_with_signals(
    node: Any,
    instance_id: str,
    node_label: str,
) -> None:
    """Run a TradingNode with SIGTERM/SIGINT handling and proper shutdown.

    Parameters
    ----------
    node
        An instantiated ``TradingNode`` with strategies/actors already added.
    instance_id
        Human-readable node identifier for log messages.
    node_label
        Short label for log messages (e.g. ``"Live"`` or ``"Sandbox"``).
    """
    shutdown_event = threading.Event()

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("Received signal %s; initiating shutdown", signal.Signals(signum).name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        node.run()
        shutdown_event.wait()
        logger.info("Shutdown event received; stopping node")
    except Exception:
        logger.exception("%s node crashed", node_label)
    finally:
        try:
            node.stop()
        except Exception:
            logger.exception("Error stopping node")
        try:
            node.dispose()
        except Exception:
            logger.exception("Error disposing node")
        logger.info("%s node %s shut down", node_label, instance_id)
