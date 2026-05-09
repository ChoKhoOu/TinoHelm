"""FastAPI application with lifespan handler for TinoHelm."""
from __future__ import annotations

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader

from tinohelm.api import deps
from tinohelm.api.routes import backtest, dashboard, data, factor, node, optimize, settings, signal, strategy, trading, universe, watchlist
from tinohelm.api.ws import hub
from tinohelm.core.bridge import EventBridge
from tinohelm.core.config import get_settings
from tinohelm.core.node_controller import NodeController
from tinohelm.backtest import consumer as bt_consumer
from tinohelm.data.worker import recover_interrupted_jobs, start_data_worker, stop_data_worker_and_wait
from tinohelm.factor.worker import recover_interrupted_jobs as recover_factor_jobs, start_factor_worker, stop_factor_worker
from tinohelm.signal.worker import recover_interrupted_jobs as recover_signal_jobs, start_signal_worker, stop_signal_worker
from tinohelm.db.session import get_session_factory
from tinohelm.strategy.registry import persist_strategies, scan_strategies

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(_api_key_header)):
    """Verify the API key if TINO_API_KEY is configured."""
    expected = os.environ.get("TINO_API_KEY", "")
    if not expected:
        return  # No key configured = auth disabled (dev mode)
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle for the TinoHelm API."""
    cfg = get_settings()
    from tinohelm.data.storage import get_active_catalog_root
    catalog_root = get_active_catalog_root(cfg)

    # ---- startup ----
    logger.info("TinoHelm API starting up")
    deps.set_startup_time(time.time())

    # Redis (async)
    redis_client = aioredis.from_url(cfg.redis.url)
    deps.set_redis(redis_client)

    # NodeController (trading-node lifecycle publisher only — backtest
    # worker pool moved to the async consumer pool below).
    nc = NodeController(
        redis_url=cfg.redis.url,
        catalog_path=str(catalog_root),
        artifacts_path=str(cfg.paths.artifacts),
        db_url=cfg.database.url,
    )
    deps.set_node_controller(nc)

    # Scan strategies directory and persist to DB
    strategies = scan_strategies(cfg.paths.strategies)
    logger.info("Discovered %d strategies", len(strategies))
    if strategies:
        async with get_session_factory()() as db:
            await persist_strategies(db, strategies)

    # EventBridge
    bridge = EventBridge(cfg.redis.url)
    await bridge.start()
    deps.set_event_bridge(bridge)

    # Recover stuck backtest runs (re-queue to Redis / legacy → failed)
    await bt_consumer.recover_interrupted_runs(redis_client)

    # Data-fetch worker (async, in-process)
    await recover_interrupted_jobs(redis_client)
    start_data_worker(redis_url=cfg.redis.url, catalog_path=str(catalog_root))

    # Factor worker (async, in-process)
    await recover_factor_jobs(redis_client)
    start_factor_worker(redis_url=cfg.redis.url)

    # Signal worker (async, in-process)
    await recover_signal_jobs(redis_client)
    start_signal_worker(redis_url=cfg.redis.url)

    # Backtest consumer pool (N long-running tasks; each launches a fresh
    # runner_cli subprocess per job — fresh NT Rust runtime per run).
    consumer_tasks, consumer_rds = await bt_consumer.start_consumers(
        n=cfg.backtest.max_concurrent,
        redis_url=cfg.redis.url,
        catalog_path=str(catalog_root),
        artifacts_path=str(cfg.paths.artifacts),
        db_url=cfg.database.url,
    )

    logger.info("TinoHelm API ready")

    yield

    # ---- shutdown ----
    logger.info("TinoHelm API shutting down")
    await stop_data_worker_and_wait(timeout=30.0)
    stop_factor_worker()
    stop_signal_worker()

    from .routes.optimize import cleanup_optimizer_processes
    cleanup_optimizer_processes()

    await bt_consumer.stop_consumers(consumer_tasks, consumer_rds, timeout=30.0)
    nc.shutdown()
    await bridge.stop()
    await redis_client.close()

    logger.info("TinoHelm API shutdown complete")


def create_app() -> FastAPI:
    """Build and return the configured FastAPI application."""
    app = FastAPI(
        title="TinoHelm",
        description="Algo-trading orchestration platform",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — origins configurable via settings (default: ["*"])
    cfg = get_settings()
    if "*" in cfg.server.cors_origins:
        allow_credentials = False
    else:
        allow_credentials = True

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.server.cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include all route modules
    # State-changing routers require API key authentication
    _auth_deps = [Depends(verify_api_key)]
    app.include_router(backtest.router, dependencies=_auth_deps)
    app.include_router(optimize.router, dependencies=_auth_deps)
    app.include_router(node.router, dependencies=_auth_deps)
    app.include_router(settings.router, dependencies=_auth_deps)
    app.include_router(watchlist.router, dependencies=_auth_deps)
    # Read-only routers — no auth required
    app.include_router(strategy.router)
    app.include_router(data.router)
    app.include_router(factor.router, dependencies=_auth_deps)
    app.include_router(signal.router, dependencies=_auth_deps)
    app.include_router(universe.router, dependencies=_auth_deps)
    app.include_router(trading.router)
    app.include_router(dashboard.router)
    app.include_router(hub.router)

    return app


app = create_app()
