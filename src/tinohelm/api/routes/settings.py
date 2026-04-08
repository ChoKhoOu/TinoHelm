"""Settings and health-check API routes."""
from __future__ import annotations

import logging
import platform
import sys
import time
from importlib.metadata import version as pkg_version

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

import httpx

from tinohelm.api.deps import get_db, get_redis, get_settings_dep, get_process_manager, get_startup_time
from tinohelm.core.audit import log_audit
from tinohelm.core.config import Settings
from tinohelm.core.process_manager import ProcessManager

EXCHANGE_PING_URLS: list[tuple[str, str]] = [
    ("Binance", "https://fapi.binance.com/fapi/v1/ping"),
]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])


# ---- response schemas ----

class ServiceHealth(BaseModel):
    """Health status of a single service."""

    status: str  # "ok" | "error"
    detail: str | None = None


class HealthResponse(BaseModel):
    """Aggregated health check response."""

    status: str  # "healthy" | "degraded"
    uptime_seconds: int
    postgres: ServiceHealth
    redis: ServiceHealth
    nodes: dict
    nautilus_version: str = "unknown"
    python_version: str = ""
    redis_version: str = "unknown"
    platform_version: str = "0.1.0"


class RiskLimitsRequest(BaseModel):
    """Request body for PUT /settings/risk-limits."""

    max_position_size: float = Field(default=100000, gt=0)
    max_daily_loss: float = Field(default=10000, gt=0)
    max_order_value: float = Field(default=50000, gt=0)
    max_leverage: float = Field(default=20, gt=0, le=125)


# ---- helpers ----

def _mask_key(value: str) -> str:
    """Mask an API key, showing only the first 4 and last 4 characters."""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


# ---- routes ----

@router.get("/settings")
async def get_current_settings(
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Return current settings with masked API keys and system version info."""
    return {
        "server": settings.server.model_dump(),
        "database": {"url": "****"},
        "redis": {"url": "****"},
        "binance": {
            "api_key": _mask_key(settings.binance.api_key) if settings.binance.api_key else "",
            "api_secret": _mask_key(settings.binance.api_secret) if settings.binance.api_secret else "",
            "account_type": settings.binance.account_type,
            "testnet": settings.binance.testnet,
        },
        "paths": {k: str(v) for k, v in settings.paths.model_dump().items()},
        "backtest": settings.backtest.model_dump(),
        "system": {
            "python": sys.version,
            "platform": platform.platform(),
        },
    }


@router.put("/settings/risk-limits")
async def update_risk_limits(
    body: RiskLimitsRequest,
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Update global risk limits. Stores in Redis and logs the change."""
    await rds.set("tino:risk_limits", body.model_dump_json())
    await log_audit(db, "update_risk_limits", {"new_limits": body.model_dump()})
    return {"message": "Risk limits updated", "limits": body.model_dump()}


@router.get("/health", response_model=HealthResponse)
async def health_check(
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
    pm: ProcessManager = Depends(get_process_manager),
) -> HealthResponse:
    """Health check: verify Postgres, Redis, and node status."""
    # Postgres
    pg_health: ServiceHealth
    try:
        await db.execute(text("SELECT 1"))
        pg_health = ServiceHealth(status="ok")
    except Exception as exc:
        logger.error("Postgres health check failed: %s", exc)
        pg_health = ServiceHealth(status="error", detail="Connection failed")

    # Redis
    redis_health: ServiceHealth
    redis_ver = "unknown"
    try:
        pong = await rds.ping()
        redis_health = ServiceHealth(status="ok" if pong else "error")
        info = await rds.info("server")
        redis_ver = info.get("redis_version", "unknown")
    except Exception as exc:
        logger.error("Redis health check failed: %s", exc)
        redis_health = ServiceHealth(status="error", detail="Connection failed")

    # Nodes
    nodes = pm.get_status()

    overall = "healthy"
    if pg_health.status != "ok" or redis_health.status != "ok":
        overall = "degraded"

    # Nautilus version
    try:
        import nautilus_trader
        nautilus_ver = nautilus_trader.__version__
    except Exception:
        nautilus_ver = "unknown"

    # Platform version
    try:
        platform_ver = pkg_version("tinohelm")
    except Exception:
        platform_ver = "0.1.0"

    startup_time = get_startup_time()
    uptime = int(time.time() - startup_time) if startup_time > 0 else 0

    return HealthResponse(
        status=overall,
        uptime_seconds=uptime,
        postgres=pg_health,
        redis=redis_health,
        nodes=nodes,
        nautilus_version=nautilus_ver,
        python_version=sys.version.split()[0],
        redis_version=redis_ver,
        platform_version=platform_ver,
    )


class ExchangeLatency(BaseModel):
    name: str
    latency_ms: float | None = None
    reachable: bool = True


@router.get("/exchanges/latency", response_model=list[ExchangeLatency])
async def exchange_latency() -> list[ExchangeLatency]:
    """Ping exchange REST endpoints and return latency in ms."""
    results: list[ExchangeLatency] = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in EXCHANGE_PING_URLS:
            try:
                t0 = time.monotonic()
                resp = await client.get(url)
                elapsed = (time.monotonic() - t0) * 1000 / 2  # one-way latency
                results.append(ExchangeLatency(
                    name=name,
                    latency_ms=round(elapsed, 1),
                    reachable=resp.status_code == 200,
                ))
            except Exception:
                results.append(ExchangeLatency(name=name, latency_ms=None, reachable=False))
    return results
