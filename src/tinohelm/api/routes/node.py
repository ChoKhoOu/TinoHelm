"""Node management API routes."""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.api.deps import get_db, get_process_manager
from tinohelm.core.config import get_settings
from tinohelm.core.process_manager import ProcessManager
from tinohelm.db.models import Position

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/node", tags=["node"])


# ---- request / response schemas ----

class StartNodeRequest(BaseModel):
    """Request body for POST /start."""

    mode: Literal["sandbox", "live"]
    strategies: list[str] = []
    portfolio_config: str | None = None


class StopNodeRequest(BaseModel):
    """Request body for POST /stop."""

    mode: Literal["sandbox", "live"]


class KillSwitchRequest(BaseModel):
    """Request body for POST /kill."""

    level: Literal[1, 2, 3]
    mode: Literal["sandbox", "live"] = "live"
    strategy_id: str | None = None


# ---- routes ----

@router.post("/start")
async def start_node(
    body: StartNodeRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Write trading node config to Redis.

    The node Docker container must be started separately:
      docker compose --profile {mode} up -d
    """
    try:
        result = await asyncio.to_thread(
            pm.start_node, body.mode, body.strategies,
            portfolio_config=body.portfolio_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "status": result["status"],
        "mode": body.mode,
        "config_version": result["config_version"],
        "message": f"Config written. Start node: docker compose --profile {body.mode} up -d",
    }


@router.post("/stop")
async def stop_node(
    body: StopNodeRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Stop a TradingNode subprocess gracefully."""
    await asyncio.to_thread(pm.stop_node, body.mode)
    return {"status": "ok", "mode": body.mode, "message": f"{body.mode} node stopped"}


@router.post("/kill")
async def kill_switch(
    body: KillSwitchRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Execute an emergency kill switch."""
    try:
        await asyncio.to_thread(pm.kill_switch, level=body.level, node_type=body.mode, strategy_id=body.strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "level": body.level, "mode": body.mode}


@router.get("/status")
async def node_status(
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the status of all managed nodes, backtest workers, and risk metrics."""
    status = await asyncio.to_thread(pm.get_status)

    # Compute risk metrics from positions
    total_exposure = 0.0
    try:
        stmt = select(Position.quantity, Position.avg_price)
        rows = (await db.execute(stmt)).all()
        for row in rows:
            qty = float(row.quantity)
            price = float(row.avg_price)
            total_exposure += abs(qty * price)
    except Exception as exc:
        logger.error("Failed to compute risk metrics: %s", exc)

    cfg = get_settings()
    base_capital = cfg.risk.base_capital
    var_multiplier = cfg.risk.var_multiplier
    status["risk_metrics"] = {
        "total_exposure": round(total_exposure, 2),
        "margin_used_pct": round(total_exposure / base_capital * 100, 2),
        "leverage": round(total_exposure / base_capital, 4) if total_exposure > 0 else 0.0,
        "max_drawdown": 0.0,
        "daily_var": round(total_exposure * var_multiplier, 2),
    }

    return status
