"""Node management API routes."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

import redis as redis_lib
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

class KillSwitchRequest(BaseModel):
    """Request body for POST /kill."""

    level: Literal[1, 2, 3]
    mode: Literal["sandbox", "live"] = "live"
    strategy_id: str | None = None


class LifecycleRequest(BaseModel):
    """Request body for POST /lifecycle."""

    action: Literal["pause", "resume", "flatten", "halt", "unhalt", "shutdown"]
    mode: Literal["sandbox", "live"] = "live"
    strategy_id: str | None = None


# ---- routes ----

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


@router.post("/lifecycle")
async def lifecycle_command(
    body: LifecycleRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Execute a lifecycle command (pause/resume/flatten/halt/unhalt/shutdown)."""
    try:
        await asyncio.to_thread(
            pm.lifecycle_command,
            action=body.action,
            node_type=body.mode,
            strategy_id=body.strategy_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "action": body.action, "mode": body.mode}


@router.get("/lifecycle/state")
async def lifecycle_state(
    mode: Literal["sandbox", "live"] = "live",
) -> dict:
    """Return lifecycle state (trading_state, strategy_states, paused list)."""
    settings = get_settings()
    r = redis_lib.Redis.from_url(settings.redis.url, decode_responses=True)
    try:
        raw = r.get(f"tino:{mode}:lifecycle_state")
        if raw:
            return json.loads(raw)
        return {"trading_state": "unknown", "strategy_states": {}, "paused": []}
    finally:
        r.close()


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
