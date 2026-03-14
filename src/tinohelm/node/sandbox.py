"""Sandbox TradingNode entry-point.

Spawned as a Docker container via ``sandbox_main.py``.  The node uses Binance
*testnet* data with a **simulated** execution client so no real orders are
sent.

Strategy/actor loading, BridgeActor wiring, and signal-based shutdown are
handled by the shared ``_common`` module.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_node(config: dict[str, Any]) -> None:
    """Entry-point executed inside the sandbox-node Docker container.

    Parameters
    ----------
    config
        Serialisable dict produced by
        :func:`tinohelm.node.factory.build_trading_node_config`.
    """
    # ---- Lazy imports (heavy nautilus deps only in the subprocess) --------
    from nautilus_trader.adapters.binance.config import BinanceDataClientConfig
    from nautilus_trader.config import (
        CacheConfig,
        DatabaseConfig,
        InstrumentProviderConfig,
        LiveDataEngineConfig,
        LiveExecEngineConfig,
        LiveRiskEngineConfig,
        LoggingConfig,
        TradingNodeConfig,
    )
    from nautilus_trader.live.node import TradingNode

    from tinohelm.node._common import load_components, run_with_signals

    instance_id = config["instance_id"]

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{instance_id}] %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting sandbox node %s", instance_id)

    # ---- Build NautilusTrader config -------------------------------------
    redis_host = config.get("redis_host", "localhost")
    redis_port = config.get("redis_port", 6379)

    node_config = TradingNodeConfig(
        trader_id=config["trader_id"],
        logging=LoggingConfig(log_level="INFO"),
        cache=CacheConfig(
            database=DatabaseConfig(
                type="redis",
                host=redis_host,
                port=redis_port,
                timeout=2,
            ),
            encoding="msgpack",
            buffer_interval_ms=100,
            flush_on_start=True,  # Clean slate each restart — sandbox uses simulated execution
            use_trader_prefix=True,
        ),
        data_engine=LiveDataEngineConfig(
            qc_on_start=True,
        ),
        exec_engine=LiveExecEngineConfig(
            # Sandbox uses simulated exchange — no reconciliation needed
            reconciliation=False,
            # Memory management for long paper-trading sessions
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
                testnet=True,  # Always testnet for sandbox
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        # Sandbox uses simulated exec -- no exec_clients configured, which
        # causes NautilusTrader to use the built-in simulated exchange.
        exec_clients={},
    )

    node = TradingNode(config=node_config)
    load_components(node, config)
    run_with_signals(node, instance_id, "Sandbox")
