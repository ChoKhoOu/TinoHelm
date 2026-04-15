"""Node management API routes."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import yaml

from tinohelm.api.deps import get_db, get_process_manager, get_redis, get_settings_dep
from tinohelm.core.config import get_settings, Settings
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


class StrategyLifecycleRequest(BaseModel):
    """Request body for portfolio lifecycle commands."""
    name: str
    mode: Literal["sandbox", "live"] = "live"


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
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Return lifecycle state (trading_state, strategy_states, paused list)."""
    raw = await rds.get(f"tino:{mode}:lifecycle_state")
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    return {"trading_state": "unknown", "strategy_states": {}, "paused": []}


def _enrich_strategy_meta(strategies: dict, settings: Settings) -> dict:
    """Enrich each strategy entry with symbols/interval from portfolio.yaml."""
    strategies_dir = Path(settings.paths.strategies)
    for name, info in strategies.items():
        yaml_path = strategies_dir / name / "portfolio.yaml"
        if yaml_path.exists():
            try:
                with open(yaml_path) as f:
                    cfg = yaml.safe_load(f) or {}
                info["symbols"] = cfg.get("symbols", [])
                info["interval"] = cfg.get("interval", "")
            except Exception:
                info.setdefault("symbols", [])
                info.setdefault("interval", "")
        else:
            info.setdefault("symbols", [])
            info.setdefault("interval", "")
    return strategies


@router.get("/strategies")
async def list_strategies(
    mode: Literal["sandbox", "live"] = "live",
    rds: aioredis.Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Return all strategies with their state and portfolio metadata."""
    raw = await rds.get(f"tino:{mode}:strategy_registry")
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        data = json.loads(raw)
        strategies = data.get("strategies", {})
        return {"strategies": _enrich_strategy_meta(strategies, settings)}
    # Fallback: try heartbeat
    hb_raw = await rds.get(f"tino:heartbeat:{mode}")
    if hb_raw:
        if isinstance(hb_raw, bytes):
            hb_raw = hb_raw.decode()
        hb = json.loads(hb_raw)
        strategies = hb.get("strategies", {})
        return {"strategies": _enrich_strategy_meta(strategies, settings)}
    return {"strategies": {}}


@router.post("/strategy/start")
async def start_strategy(
    body: StrategyLifecycleRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Start a strategy on the node."""
    try:
        await asyncio.to_thread(
            pm.lifecycle_command,
            action="start_strategy",
            node_type=body.mode,
            strategy_name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "action": "start_strategy", "name": body.name}


@router.post("/strategy/pause")
async def pause_strategy(
    body: StrategyLifecycleRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Pause a running strategy."""
    try:
        await asyncio.to_thread(
            pm.lifecycle_command,
            action="pause_strategy",
            node_type=body.mode,
            strategy_name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "action": "pause_strategy", "name": body.name}


@router.post("/strategy/resume")
async def resume_strategy(
    body: StrategyLifecycleRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Resume a paused strategy."""
    try:
        await asyncio.to_thread(
            pm.lifecycle_command,
            action="resume_strategy",
            node_type=body.mode,
            strategy_name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "action": "resume_strategy", "name": body.name}


@router.post("/strategy/flatten-stop")
async def flatten_stop_strategy(
    body: StrategyLifecycleRequest,
    pm: ProcessManager = Depends(get_process_manager),
) -> dict:
    """Flatten positions and stop a strategy."""
    try:
        await asyncio.to_thread(
            pm.lifecycle_command,
            action="flatten_stop_strategy",
            node_type=body.mode,
            strategy_name=body.name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "action": "flatten_stop_strategy", "name": body.name}


@router.get("/data-status")
async def data_status(
    mode: Literal["sandbox", "live"] = "sandbox",
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Return data feed health status derived from heartbeat."""
    raw = await rds.get(f"tino:heartbeat:{mode}")
    if not raw:
        return {"status": "offline", "last_seen": None, "bar_types": [], "strategies": 0, "positions": 0}
    if isinstance(raw, bytes):
        raw = raw.decode()
    hb = json.loads(raw)
    # Derive strategy count from strategy_states (more reliable than cache count)
    strategy_states = hb.get("strategy_states", {})
    strategy_count = len(strategy_states) if strategy_states else hb.get("strategies", 0)
    return {
        "status": "online",
        "last_seen": hb.get("ts"),
        "bar_types": hb.get("bar_types", []),
        "strategies": strategy_count,
        "positions": hb.get("positions", 0),
        "balance_total": hb.get("balance_total"),
        "balance_free": hb.get("balance_free"),
    }


@router.get("/subscriptions")
async def subscriptions(
    mode: Literal["sandbox", "live"] = "sandbox",
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Return active data subscriptions from heartbeat and portfolio registry."""
    # Try heartbeat first
    raw = await rds.get(f"tino:heartbeat:{mode}")
    if not raw:
        return {"bar_types": [], "instruments": []}
    if isinstance(raw, bytes):
        raw = raw.decode()
    hb = json.loads(raw)
    bar_types = hb.get("bar_types", [])
    # Derive instruments from bar type strings (format: "BTCUSDT-PERP.BINANCE-5-MINUTE-...")
    instruments = sorted({bt.split(".")[0] for bt in bar_types if "." in bt})
    return {"bar_types": bar_types, "instruments": instruments}


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
        stmt = select(Position.quantity, Position.avg_px_open)
        rows = (await db.execute(stmt)).all()
        for row in rows:
            qty = float(row.quantity)
            price = float(row.avg_px_open) if row.avg_px_open is not None else 0.0
            total_exposure += abs(qty * price)
    except Exception as exc:
        logger.error("Failed to compute risk metrics: %s", exc)

    cfg = get_settings()
    base_capital = cfg.risk.base_capital
    var_multiplier = cfg.risk.var_multiplier
    status["risk_metrics"] = {
        "total_exposure": round(total_exposure, 2),
        "margin_used_pct": round(total_exposure / base_capital * 100, 2) if base_capital > 0 else 0.0,
        "leverage": round(total_exposure / base_capital, 2) if base_capital > 0 else 0.0,
        "max_drawdown": 0.0,
        "daily_var": round(total_exposure * var_multiplier, 2),
    }

    return status


# ---- paper trading config / reset ----

class PaperConfigUpdate(BaseModel):
    """Fields that can be updated in paper trading config."""
    starting_capital: float | None = None
    fee_rate: float | None = None
    slippage_model: str | None = None
    latency_ms: int | None = None


@router.get("/paper-config")
async def get_paper_config(
    mode: Literal["sandbox", "live"] = "sandbox",
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Return current paper trading configuration."""
    raw = await rds.get(f"tino:{mode}:paper_config")
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    return {
        "starting_capital": 10000.0,
        "fee_rate": 0.0004,
        "slippage_model": "binance-default",
        "latency_ms": 0,
    }


@router.put("/paper-config")
async def update_paper_config(
    body: PaperConfigUpdate,
    mode: Literal["sandbox", "live"] = "sandbox",
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Update paper trading configuration. Takes effect on next node restart."""
    raw = await rds.get(f"tino:{mode}:paper_config")
    config: dict = {}
    if raw:
        if isinstance(raw, bytes):
            raw = raw.decode()
        config = json.loads(raw)
    updates = body.model_dump(exclude_none=True)
    config.update(updates)
    await rds.set(f"tino:{mode}:paper_config", json.dumps(config))
    return {"status": "ok", "config": config}


@router.post("/paper-reset")
async def paper_reset(
    mode: Literal["sandbox", "live"] = "sandbox",
    restart: bool = Query(False, description="Restart node after reset"),
    pm: ProcessManager = Depends(get_process_manager),
    db: AsyncSession = Depends(get_db),
    rds: aioredis.Redis = Depends(get_redis),
) -> dict:
    """Reset paper trading state: shutdown node, clear data, optionally restart."""
    # 1. Shutdown node
    try:
        await asyncio.to_thread(pm.lifecycle_command, action="shutdown", node_type=mode)
    except Exception:
        pass

    # 2. Wait for heartbeat to go stale (poll up to 15s)
    for _ in range(15):
        await asyncio.sleep(1)
        hb = await rds.get(f"tino:heartbeat:{mode}")
        if not hb:
            break

    # 3. Clear DB data
    await db.execute(text("DELETE FROM positions WHERE node_type = :nt"), {"nt": mode})
    await db.execute(text("DELETE FROM fills WHERE node_type = :nt"), {"nt": mode})
    await db.execute(text("DELETE FROM equity_snapshots WHERE node_type = :nt"), {"nt": mode})
    await db.commit()

    # 4. Clear Redis state
    keys = await rds.keys(f"tino:{mode}:*")
    if keys:
        await rds.delete(*keys)

    result: dict = {"status": "ok", "action": "reset", "mode": mode, "data_cleared": True}

    # 5. Optionally restart
    if restart:
        await asyncio.sleep(2)
        try:
            await asyncio.to_thread(pm.lifecycle_command, action="start", node_type=mode)
            result["restarted"] = True
        except Exception as e:
            result["restarted"] = False
            result["restart_error"] = str(e)

    return result
