"""Strategy management API routes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tinohelm.api.deps import get_db, get_settings_dep
from tinohelm.core.config import Settings
from tinohelm.db.models import Strategy, StrategyVersion

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


# ---- request / response schemas ----

class StrategyItem(BaseModel):
    """Single strategy in the list response."""

    id: int
    name: str
    type: str = "single"
    file_path: str
    strategy_class: str
    config_class: str
    created_at: str | None = None
    updated_at: str | None = None


class StrategyVersionItem(BaseModel):
    """A version entry for a strategy."""

    id: int
    version: int
    code_hash: str
    created_at: str | None = None


class StrategyDetail(BaseModel):
    """Detailed strategy info including version history."""

    id: int
    name: str
    type: str = "single"
    file_path: str
    strategy_class: str
    config_class: str
    created_at: str | None = None
    updated_at: str | None = None
    versions: list[StrategyVersionItem] = []


class CreateStrategyRequest(BaseModel):
    """Request body for POST /create."""

    name: str
    type: str = "strategy"



class CreateStrategyResponse(BaseModel):
    """Response for strategy/portfolio scaffold creation."""

    name: str
    file_path: str
    message: str


class RescanResponse(BaseModel):
    """Response for POST /rescan."""

    discovered: int
    strategies: list[str]


class ValidateResponse(BaseModel):
    """Response for POST /{name}/validate."""

    valid: bool
    issues: list[str] | None = None
    strategy_class: str | None = None
    config_class: str | None = None


# ---- routes ----

@router.get("", response_model=list[StrategyItem])
async def list_strategies(
    db: AsyncSession = Depends(get_db),
) -> list[StrategyItem]:
    """List all strategies from the database."""
    stmt = select(Strategy).order_by(Strategy.name)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        StrategyItem(
            id=s.id,
            name=s.name,
            type=s.type.value if s.type else "single",
            file_path=s.file_path,
            strategy_class=s.strategy_class,
            config_class=s.config_class,
            created_at=s.created_at.isoformat() if s.created_at else None,
            updated_at=s.updated_at.isoformat() if s.updated_at else None,
        )
        for s in rows
    ]


@router.get("/{name}", response_model=StrategyDetail)
async def get_strategy(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> StrategyDetail:
    """Get strategy detail including version history."""
    stmt = (
        select(Strategy)
        .options(selectinload(Strategy.versions))
        .where(Strategy.name == name)
    )
    strategy = (await db.execute(stmt)).scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

    return StrategyDetail(
        id=strategy.id,
        name=strategy.name,
        type=strategy.type.value if strategy.type else "single",
        file_path=strategy.file_path,
        strategy_class=strategy.strategy_class,
        config_class=strategy.config_class,
        created_at=strategy.created_at.isoformat() if strategy.created_at else None,
        updated_at=strategy.updated_at.isoformat() if strategy.updated_at else None,
        versions=[
            StrategyVersionItem(
                id=v.id,
                version=v.version,
                code_hash=v.code_hash,
                created_at=v.created_at.isoformat() if v.created_at else None,
            )
            for v in sorted(strategy.versions, key=lambda v: v.version, reverse=True)
        ],
    )


@router.get("/{name}/params")
async def get_strategy_params(
    name: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Get strategy parameters with optimization ranges."""
    stmt = select(Strategy).where(Strategy.name == name)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

    config_params: list[dict[str, Any]] = []
    optimize_ranges: dict[str, Any] = {}

    file_path = Path(row.file_path)

    if file_path.suffix == ".py" and file_path.exists():
        import inspect

        from tinohelm.strategy.module_loader import load_module_from_file
        from tinohelm.strategy.utils import get_config_fields, parse_optimize_ranges

        try:
            mod = load_module_from_file(file_path)

            for obj_name, obj in inspect.getmembers(mod, inspect.isclass):
                if obj.__module__ != mod.__name__:
                    continue
                for base in inspect.getmro(obj):
                    if base.__name__ == "StrategyConfig" and base.__module__.startswith("nautilus_trader"):
                        config_params = get_config_fields(obj)
                        break

            optimize_ranges = parse_optimize_ranges(getattr(mod, "OPTIMIZE", {}) or {})
        except Exception as e:
            logger.warning("Failed to inspect strategy %s: %s", name, e)

    # Filter out internal params (injected by loader)
    internal_params = {"instrument_id", "instrument_ids", "bar_type", "bar_types",
                       "symbols", "interval",
                       "order_id_tag", "manage_stop", "manage_gtd_expiry",
                       "oms_type", "external_order_claims", "manage_contingent_orders",
                       "symbol_params"}
    user_params = [p for p in config_params if p["name"] not in internal_params]

    return {
        "name": name,
        "config_params": user_params,
        "optimize_ranges": optimize_ranges,
    }


class StrategySubscription(BaseModel):
    """A data subscription entry for a strategy."""

    exchange: str = "binance"
    symbol: str
    granularity: str = "bar"  # "bar" or "tick"
    timeframe: str | None = None  # e.g. "5min", None for tick
    tick_type: str | None = None  # "trades", "quotes", "l2"
    auto: bool = True


class StrategyDefaults(BaseModel):
    """Default configuration for a strategy."""

    symbols: list[str] = []
    interval: str | None = None
    starting_balance: float | None = None
    subscriptions: list[StrategySubscription] = []


def _interval_to_timeframe(interval: str) -> str:
    """Convert interval string (e.g. '5m') to display timeframe (e.g. '5min')."""
    _map = {
        "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
        "1d": "1d",
    }
    return _map.get(interval, interval)


def _build_subscriptions(symbols: list[str], interval: str | None) -> list[StrategySubscription]:
    """Build subscription list from symbols and interval."""
    subs = []
    for sym in symbols:
        # Strip .BINANCE suffix if present
        display_sym = sym.replace(".BINANCE", "")
        if interval:
            subs.append(StrategySubscription(
                exchange="binance",
                symbol=display_sym,
                granularity="bar",
                timeframe=_interval_to_timeframe(interval),
                auto=True,
            ))
        else:
            subs.append(StrategySubscription(
                exchange="binance",
                symbol=display_sym,
                granularity="bar",
                auto=True,
            ))
    return subs


@router.get("/{name}/defaults", response_model=StrategyDefaults)
async def get_strategy_defaults(
    name: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> StrategyDefaults:
    """Get default run configuration for a strategy."""
    import yaml

    stmt = select(Strategy).where(Strategy.name == name)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

    strategies_dir = Path(settings.paths.strategies)
    symbols: list[str] = []
    interval: str | None = None

    try:
        # Case 1: portfolio folder with portfolio.yaml
        yaml_path = strategies_dir / name / "portfolio.yaml"
        if yaml_path.exists():
            with open(yaml_path) as f:
                cfg = yaml.safe_load(f) or {}
            symbols = cfg.get("symbols", [])
            interval = cfg.get("interval") or None

        else:
            # Case 2: single .py file — load module and inspect SYMBOL_PROFILES
            file_path = Path(row.file_path)
            if file_path.suffix == ".py" and file_path.exists():
                from tinohelm.strategy.module_loader import load_module_from_file

                mod = load_module_from_file(file_path)
                profiles = getattr(mod, "SYMBOL_PROFILES", None)
                if profiles and isinstance(profiles, dict):
                    # Convert Jesse-format keys (BTC-USDT) back to NT format (BTCUSDT-PERP)
                    for jesse_key in profiles:
                        parts = jesse_key.split("-")
                        if len(parts) == 2:
                            symbols.append(f"{parts[0]}{parts[1]}-PERP")
                        else:
                            symbols.append(jesse_key)
    except Exception as e:
        logger.warning("Failed to load defaults for strategy %s: %s", name, e)

    subscriptions = _build_subscriptions(symbols, interval)

    return StrategyDefaults(
        symbols=symbols,
        interval=interval,
        subscriptions=subscriptions,
    )


@router.post("/create", response_model=CreateStrategyResponse)
async def create_strategy(
    body: CreateStrategyRequest,
    settings: Settings = Depends(get_settings_dep),
) -> CreateStrategyResponse:
    """Generate a strategy scaffold file."""
    from tinohelm.strategy.scaffold import generate_scaffold

    try:
        file_path = generate_scaffold(
            name=body.name,
            strategies_dir=settings.paths.strategies,
            scaffold_type=body.type,
        )
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Strategy '{body.name}' already exists")
    except Exception as exc:
        logger.exception("Failed to create strategy scaffold: %s", body.name)
        raise HTTPException(status_code=500, detail="Internal error creating strategy")

    return CreateStrategyResponse(
        name=body.name,
        file_path=str(file_path),
        message=f"Created {body.type} strategy scaffold: {body.name}",
    )


@router.post("/{name}/open")
async def open_strategy(
    name: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Return the host-side absolute path to a strategy file/folder."""
    import os

    stmt = select(Strategy).where(Strategy.name == name)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

    file_path = Path(row.file_path).resolve()
    abs_path = str(file_path)

    # Map container path to host path if running inside Docker
    # Container: /app/tino/strategies/... → Host: ~/.tino/strategies/...
    container_strategies = str(Path(settings.paths.strategies).resolve())
    host_home = os.environ.get("HOST_HOME", "")
    if host_home:
        host_strategies = os.path.join(host_home, ".tino", "strategies")
        if abs_path.startswith(container_strategies):
            abs_path = abs_path.replace(container_strategies, host_strategies, 1)

    return {"name": name, "path": abs_path}


@router.post("/rescan", response_model=RescanResponse)
async def rescan_strategies(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> RescanResponse:
    """Re-scan strategies directory and persist any new or updated strategies.

    Use this in production to hot-load new strategies without restarting.
    """
    from tinohelm.strategy.registry import persist_strategies, scan_strategies

    discovered = scan_strategies(settings.paths.strategies)
    await persist_strategies(db, discovered, rebuild=True)

    return RescanResponse(
        discovered=len(discovered),
        strategies=[d["name"] for d in discovered],
    )


@router.post("/{name}/validate", response_model=ValidateResponse)
async def validate_strategy_route(
    name: str,
    settings: Settings = Depends(get_settings_dep),
) -> ValidateResponse:
    """Validate a strategy file for structural correctness."""
    from tinohelm.strategy.validator import validate_strategy

    result = validate_strategy(name, settings.paths.strategies)
    if not result["valid"]:
        raise HTTPException(status_code=422, detail=result)
    return ValidateResponse(**result)
