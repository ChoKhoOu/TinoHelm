"""Factory for building serializable TradingNode config dicts."""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from tinohelm.core.config import Settings

# Redis DB isolation per node type.
_REDIS_DB_MAP: dict[str, int] = {
    "sandbox": 0,
    "live": 1,
}


def build_trading_node_config(
    node_type: str,
    strategies: list[str],
    settings: Settings,
    *,
    portfolio_config: str | None = None,
    for_redis: bool = False,
) -> dict[str, Any]:
    """Build a plain-dict config suitable for passing to a TradingNode subprocess.

    The returned dict is fully JSON-serialisable so it can safely cross the
    ``multiprocessing`` boundary.

    Parameters
    ----------
    node_type
        Either ``"sandbox"`` or ``"live"``.
    strategies
        List of importable strategy class paths, e.g.
        ``["tinohelm.strategy.my_strat:MyStrat"]``.
        Ignored when ``portfolio_config`` is provided.
    settings
        The application :class:`Settings` instance.
    portfolio_config
        Optional portfolio name or path. When provided, the node uses
        ``portfolio_loader`` for strategy/actor creation instead of
        the legacy ``strategies`` list.
    for_redis
        When ``True``, the ``binance`` dict uses a ``{"from_env": True, ...}``
        sentinel instead of embedding credentials.  The standalone entry-points
        (``sandbox_main`` / ``live_main``) then inject the real credentials
        from environment variables before calling ``run_node``.

    Returns
    -------
    dict
        A config dict consumed by ``tinohelm.node.sandbox.run_node`` or
        ``tinohelm.node.live.run_node``.
    """
    if node_type not in _REDIS_DB_MAP:
        raise ValueError(f"Unknown node_type {node_type!r}")

    instance_id = f"{node_type}-{uuid4()}"
    redis_db = _REDIS_DB_MAP[node_type]

    # Parse Redis host/port from URL for direct connection metadata.
    parsed = urlparse(settings.redis.url)
    redis_host = parsed.hostname or "localhost"
    redis_port = parsed.port or 6379

    # Binance credentials: embed directly or use from-env sentinel.
    if for_redis:
        binance_config: dict[str, Any] = {
            "from_env": True,
            "account_type": settings.binance.account_type,
        }
    else:
        binance_config = {
            "api_key": settings.binance.api_key,
            "api_secret": settings.binance.api_secret,
            "account_type": settings.binance.account_type,
        }

    # Base config shared by both node types.
    config: dict[str, Any] = {
        "node_type": node_type,
        "instance_id": instance_id,
        "config_version": str(int(time.time() * 1000)),
        "redis_url": settings.redis.url,
        "redis_host": redis_host,
        "redis_port": redis_port,
        "redis_db": redis_db,
        "db_url": settings.database.url,
        "strategies": strategies,
        "portfolio_config": portfolio_config,
        "binance": binance_config,
        "catalog_path": str(settings.paths.catalog),
        "log_path": str(settings.paths.logs),
    }

    if node_type == "sandbox":
        config["trader_id"] = "SANDBOX-001"
        config["testnet"] = True
    elif node_type == "live":
        config["trader_id"] = "LIVE-001"
        config["testnet"] = settings.binance.testnet
        # Reconciliation: align internal state with exchange on startup and continuously
        config["reconciliation"] = True
        config["reconciliation_lookback_mins"] = 1440  # 24h lookback

    return config
