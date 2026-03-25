"""Live TradingNode entry-point.

Spawned as a Docker container via ``live_main.py``.  The node connects to
Binance with a **real** execution client.  The ``testnet`` flag is read from
the config dict so the same code works for both testnet dry-runs and
production trading.

Strategy/actor loading, BridgeActor wiring, and signal-based shutdown are
handled by the shared ``_common`` module.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def run_node(config: dict[str, Any]) -> None:
    """Entry-point executed inside the live-node Docker container.

    Parameters
    ----------
    config
        Serialisable dict produced by
        :func:`tinohelm.node.factory.build_trading_node_config`.
    """
    # ---- Lazy imports (heavy nautilus deps only in the subprocess) --------
    from nautilus_trader.adapters.binance import BINANCE
    from nautilus_trader.adapters.binance.common.enums import BinanceAccountType
    from nautilus_trader.adapters.binance.config import (
        BinanceDataClientConfig,
        BinanceExecClientConfig,
    )
    from nautilus_trader.adapters.binance.factories import (
        BinanceLiveDataClientFactory,
        BinanceLiveExecClientFactory,
    )
    from nautilus_trader.config import (
        CacheConfig,
        DatabaseConfig,
        ImportableControllerConfig,
        InstrumentProviderConfig,
        LiveDataEngineConfig,
        LiveExecEngineConfig,
        LiveRiskEngineConfig,
        LoggingConfig,
        TradingNodeConfig,
    )
    from nautilus_trader.live.node import TradingNode

    from tinohelm.node._common import inject_lifecycle_deps, load_components, run_with_signals

    instance_id = config["instance_id"]
    testnet = config.get("testnet", False)

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{instance_id}] %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting live node %s (testnet=%s)", instance_id, testnet)

    # ---- Build NautilusTrader config -------------------------------------
    redis_host = config.get("redis_host", "localhost")
    redis_port = config.get("redis_port", 6379)

    reconciliation = config.get("reconciliation", True)
    reconciliation_lookback_mins = config.get("reconciliation_lookback_mins", 1440)

    node_config = TradingNodeConfig(
        trader_id=config["trader_id"],
        logging=LoggingConfig(log_level="INFO"),
        controller=ImportableControllerConfig(
            controller_path="tinohelm.node.controller:TinoController",
            config_path="nautilus_trader.config:ActorConfig",
            config={},
        ),
        cache=CacheConfig(
            database=DatabaseConfig(
                type="redis",
                host=redis_host,
                port=redis_port,
                timeout=2,
            ),
            encoding="msgpack",
            timestamps_as_iso8601=True,
            buffer_interval_ms=100,
            flush_on_start=False,  # Recover state on restart for live trading
            use_trader_prefix=True,
        ),
        data_engine=LiveDataEngineConfig(),
        exec_engine=LiveExecEngineConfig(
            # -- State snapshots (crash recovery) --
            snapshot_orders=True,
            snapshot_positions=True,
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
                account_type=BinanceAccountType[config["binance"]["account_type"]],
                testnet=testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            "BINANCE": BinanceExecClientConfig(
                api_key=config["binance"]["api_key"],
                api_secret=config["binance"]["api_secret"],
                account_type=BinanceAccountType[config["binance"]["account_type"]],
                testnet=testnet,
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
    )

    node = TradingNode(config=node_config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    bridge_actor = load_components(node, config)
    node.build()
    inject_lifecycle_deps(node, bridge_actor)
    run_with_signals(node, instance_id, "Live")
