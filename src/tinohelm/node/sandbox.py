"""Sandbox TradingNode entry-point.

Spawned as a Docker container via ``sandbox_main.py``.  The node connects to
Binance Demo (demo.binance.com) for both market data **and** order execution.
No real money is at risk — the balance comes from the Binance Demo account.

Strategy/actor loading, 5-actor wiring, and signal-based shutdown are
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
    from decimal import Decimal

    from nautilus_trader.adapters.binance import BINANCE
    from nautilus_trader.adapters.binance.common.enums import (
        BinanceAccountType,
        BinanceEnvironment,
    )
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

    from tinohelm.node._common import build_cache_config, inject_lifecycle_deps, load_components, run_with_signals

    instance_id = config["instance_id"]

    logging.basicConfig(
        level=logging.INFO,
        format=f"%(asctime)s [{instance_id}] %(levelname)s %(name)s: %(message)s",
    )
    logger.info("Starting sandbox node %s", instance_id)

    # ---- Build NautilusTrader config -------------------------------------
    redis_host = config.get("redis_host", "localhost")
    redis_port = config.get("redis_port", 6379)
    redis_password = config.get("redis_password", "") or None

    # Layer 3: Global safety net — NT RiskEngine pre-trade checks
    _re_cfg = config.get("risk_engine", {})

    node_config = TradingNodeConfig(
        trader_id=config["trader_id"],
        logging=LoggingConfig(log_level="INFO"),
        controller=ImportableControllerConfig(
            controller_path="tinohelm.node.controller:TinoController",
            config_path="nautilus_trader.config:ActorConfig",
            config={},
        ),
        cache=build_cache_config(redis_host, redis_port, redis_password, is_sandbox=True),
        data_engine=LiveDataEngineConfig(),
        exec_engine=LiveExecEngineConfig(
            # State snapshots (crash recovery)
            snapshot_orders=True,
            snapshot_positions=True,
            # Reconcile with Binance Demo on startup
            reconciliation=True,
            reconciliation_lookback_mins=1440,  # 24h lookback
            # Overfill protection (WebSocket reconnect can replay fills)
            allow_overfills=True,
            # Continuous reconciliation
            inflight_check_interval_ms=2000,
            open_check_interval_secs=10.0,
            open_check_lookback_mins=60,
            reconciliation_startup_delay_secs=10.0,
            # Memory management for long paper-trading sessions
            purge_closed_orders_interval_mins=15,
            purge_closed_orders_buffer_mins=60,
            purge_closed_positions_interval_mins=15,
            purge_closed_positions_buffer_mins=60,
        ),
        risk_engine=LiveRiskEngineConfig(
            max_order_submit_rate=_re_cfg.get("max_order_submit_rate", "100/00:00:01"),
            max_order_modify_rate=_re_cfg.get("max_order_modify_rate", "100/00:00:01"),
        ),
        data_clients={
            "BINANCE": BinanceDataClientConfig(
                api_key=config["binance"]["api_key"],
                api_secret=config["binance"]["api_secret"],
                account_type=BinanceAccountType[config["binance"]["account_type"]],
                environment=BinanceEnvironment.DEMO,
                # NT 1.224.0 maps DEMO to testnet URLs for futures — override manually
                base_url_http="https://demo-fapi.binance.com",
                base_url_ws="wss://demo-fstream.binance.com",
                instrument_provider=InstrumentProviderConfig(load_all=True),
            ),
        },
        exec_clients={
            "BINANCE": BinanceExecClientConfig(
                api_key=config["binance"]["api_key"],
                api_secret=config["binance"]["api_secret"],
                account_type=BinanceAccountType[config["binance"]["account_type"]],
                environment=BinanceEnvironment.DEMO,
                # NT 1.224.0 maps DEMO to testnet URLs for futures — override manually.
                # Without these overrides NT falls back to testnet.binancefuture.com /
                # stream.binancefuture.com which use a private CA ("Think Beyond Pte. Ltd.")
                # that rustls (webpki-roots) does not trust, causing UnknownIssuer TLS errors.
                base_url_http="https://demo-fapi.binance.com",
                base_url_ws="wss://demo-fstream.binance.com",
                base_url_ws_stream="wss://demo-fstream.binance.com",
                instrument_provider=InstrumentProviderConfig(load_all=True),
                # Binance Hedge Mode does not support reduce_only; disable it.
                # NT virtual hedging (use_position_ids=True) handles position sides.
                use_reduce_only=False,
            ),
        },
    )

    node = TradingNode(config=node_config)
    node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(BINANCE, BinanceLiveExecClientFactory)
    actors = load_components(node, config)
    node.build()
    inject_lifecycle_deps(node, actors["command"], actors.get("health"))
    run_with_signals(node, instance_id, "Sandbox")
