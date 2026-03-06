"""Live TradingNode entry-point.

Spawned as a ``multiprocessing.Process`` by the ProcessManager.  The node
connects to Binance with a **real** execution client.  The ``testnet`` flag
is read from the config dict so the same code works for both testnet dry-runs
and production trading.

Strategy/actor loading is handled by the shared portfolio_loader module.
Redis event bridging and heartbeat are handled by BridgeActor.
"""
from __future__ import annotations

import logging
import signal
from typing import Any

logger = logging.getLogger(__name__)


def run_node(config: dict[str, Any]) -> None:
    """Entry-point executed in a child process.

    Parameters
    ----------
    config
        Serialisable dict produced by
        :func:`tinohelm.node.factory.build_trading_node_config`.
    """
    # ---- Lazy imports (heavy nautilus deps only in the subprocess) --------
    from nautilus_trader.config import (
        InstrumentProviderConfig,
        LiveDataEngineConfig,
        LiveExecEngineConfig,
        LiveRiskEngineConfig,
        LoggingConfig,
        TradingNodeConfig,
    )
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.adapters.binance.config import (
        BinanceDataClientConfig,
        BinanceExecClientConfig,
    )
    from nautilus_trader.adapters.binance.factories import (
        BinanceLiveExecClientFactory,
    )

    from tinohelm.node.bridge_actor import BridgeActor, BridgeActorConfig
    from tinohelm.portfolio.loader import create_strategies, create_actors
    from tinohelm.portfolio.config import load_portfolio_config

    import threading

    node_type = config["node_type"]
    instance_id = config["instance_id"]
    testnet = config.get("testnet", False)

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{instance_id}] %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting live node %s (testnet=%s)", instance_id, testnet)

    # ---- Build NautilusTrader config -------------------------------------
    reconciliation = config.get("reconciliation", True)
    reconciliation_lookback_mins = config.get("reconciliation_lookback_mins", 1440)

    node_config = TradingNodeConfig(
        trader_id=config["trader_id"],
        logging=LoggingConfig(log_level="INFO"),
        data_engine=LiveDataEngineConfig(
            qc_on_start=True,
        ),
        exec_engine=LiveExecEngineConfig(
            # -- Reconciliation (startup) --
            reconciliation=reconciliation,
            reconciliation_lookback_mins=reconciliation_lookback_mins,
            # -- Overfill protection (WebSocket reconnect can cause duplicate fills) --
            allow_overfills=True,
            # -- Continuous reconciliation (runtime) --
            inflight_check_interval_ms=2000,    # Check in-flight orders every 2s
            inflight_check_threshold_ms=5000,   # Flag orders unconfirmed >5s
            open_check_interval_secs=10.0,      # Poll open orders every 10s
            open_check_lookback_mins=60,         # NEVER set below 60 per NT docs
            reconciliation_startup_delay_secs=10.0,  # Wait 10s before continuous recon
            # -- Memory management (prevent OOM in long-running sessions) --
            purge_closed_orders_interval_mins=15,
            purge_closed_orders_buffer_mins=60,
            purge_closed_positions_interval_mins=15,
            purge_closed_positions_buffer_mins=60,
        ),
        risk_engine=LiveRiskEngineConfig(),
        data_clients={
            "BINANCE": BinanceDataClientConfig(
                api_key=config["binance"]["api_key"],
                api_secret=config["binance"]["api_secret"],
                account_type=config["binance"]["account_type"],
                testnet=testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            "BINANCE": BinanceExecClientConfig(
                api_key=config["binance"]["api_key"],
                api_secret=config["binance"]["api_secret"],
                account_type=config["binance"]["account_type"],
                testnet=testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
    )

    node = TradingNode(config=node_config)

    # ---- Load strategies and actors via portfolio_loader -----------------
    portfolio_config_name = config.get("portfolio_config")
    if portfolio_config_name:
        portfolio_cfg = load_portfolio_config(portfolio_config_name)
        strategy_instances = create_strategies(portfolio_cfg)
        actor_instances = create_actors(portfolio_cfg)
    else:
        # Legacy: load from strategy paths list
        from nautilus_trader.config import ImportableStrategyConfig

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

    # ---- Wire BridgeActor for Redis event bridging and commands ----------
    redis_url = config["redis_url"]
    redis_db = config.get("redis_db", 0)
    bridge_config = BridgeActorConfig(
        redis_url=f"{redis_url}/{redis_db}" if "/" not in redis_url.split("//")[-1] else redis_url,
        node_type=node_type,
    )
    bridge_actor = BridgeActor(config=bridge_config)
    node.trader.add_actor(bridge_actor)

    # ---- Signal handling -------------------------------------------------
    shutdown_event = threading.Event()

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("Received signal %s; initiating shutdown", signal.Signals(signum).name)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # ---- Run the node ----------------------------------------------------
    try:
        node.run()
        # Block until we receive a shutdown signal.
        shutdown_event.wait()
        logger.info("Shutdown event received; stopping node")
    except Exception:
        logger.exception("Live node crashed")
    finally:
        try:
            node.dispose()
        except Exception:
            logger.exception("Error disposing node")
        logger.info("Live node %s shut down", instance_id)
