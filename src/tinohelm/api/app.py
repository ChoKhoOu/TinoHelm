"""FastAPI application with lifespan handler for TinoHelm."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tinohelm.api import deps
from tinohelm.api.routes import backtest, dashboard, data, node, optimize, settings, strategy, trading, watchlist
from tinohelm.api.ws import hub
from tinohelm.core.bridge import EventBridge
from tinohelm.core.config import get_settings
from tinohelm.core.process_manager import ProcessManager
from tinohelm.core.watchdog import Watchdog
from tinohelm.db.models import Base
from tinohelm.db.session import get_engine, get_session_factory
from tinohelm.strategy.registry import persist_strategies, scan_strategies

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle for the TinoHelm API."""
    cfg = get_settings()

    # ---- startup ----
    logger.info("TinoHelm API starting up")
    deps.set_startup_time(time.time())

    # Ensure all tables exist (safe if already created by Alembic)
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ensured")

    # Redis (async)
    redis_client = aioredis.from_url(cfg.redis.url)
    deps.set_redis(redis_client)

    # ProcessManager (uses sync redis internally)
    pm = ProcessManager(
        redis_url=cfg.redis.url,
        catalog_path=str(cfg.paths.catalog),
        artifacts_path=str(cfg.paths.artifacts),
        db_url=cfg.database.url,
    )
    deps.set_process_manager(pm)

    # Watchdog
    watchdog = Watchdog(pm, redis_url=cfg.redis.url)
    watchdog.start()

    # Start backtest workers
    pm.start_workers(cfg.backtest.max_workers)

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

    logger.info("TinoHelm API ready")

    yield

    # ---- shutdown ----
    logger.info("TinoHelm API shutting down")

    await watchdog.stop()
    pm.shutdown_all()
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include all route modules
    app.include_router(backtest.router)
    app.include_router(optimize.router)
    app.include_router(node.router)
    app.include_router(strategy.router)
    app.include_router(data.router)
    app.include_router(trading.router)
    app.include_router(dashboard.router)
    app.include_router(settings.router)
    app.include_router(watchlist.router)
    app.include_router(hub.router)

    return app


app = create_app()
