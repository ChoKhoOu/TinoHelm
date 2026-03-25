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
    type: Literal["bar", "tick"] = "bar"


class CreateStrategyResponse(BaseModel):
    """Response for strategy scaffold creation."""

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
        import importlib.util
        import inspect
        import sys

        from tinohelm.strategy.utils import get_config_fields

        module_name = f"_params_discover_{file_path.stem}"
        str_dir = str(file_path.parent.resolve())
        added_path = False
        if str_dir not in sys.path:
            sys.path.insert(0, str_dir)
            added_path = True

        if module_name in sys.modules:
            del sys.modules[module_name]

        try:
            spec = importlib.util.spec_from_file_location(module_name, str(file_path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)

                for obj_name, obj in inspect.getmembers(mod, inspect.isclass):
                    if obj.__module__ != module_name:
                        continue
                    for base in inspect.getmro(obj):
                        if base.__name__ == "StrategyConfig" and base.__module__.startswith("nautilus_trader"):
                            config_params = get_config_fields(obj)
                            break

                from tinohelm.strategy.utils import parse_optimize_ranges
                optimize_ranges = parse_optimize_ranges(getattr(mod, "OPTIMIZE", {}) or {})
        except Exception as e:
            logger.warning("Failed to inspect strategy %s: %s", name, e)
        finally:
            sys.modules.pop(module_name, None)
            if added_path and str_dir in sys.path:
                sys.path.remove(str_dir)

    elif file_path.name == "portfolio.yaml" and file_path.exists():
        import yaml
        from tinohelm.strategy.utils import parse_optimize_ranges
        with open(file_path) as f:
            raw = yaml.safe_load(f)
        optimize_ranges = parse_optimize_ranges((raw or {}).get("optimize", {}) or {})

    # Filter out internal params (injected by loader)
    internal_params = {"instrument_id", "instrument_ids", "bar_type", "bar_types",
                       "_bar_type_map", "order_id_tag", "manage_stop", "manage_gtd_expiry",
                       "oms_type", "external_order_claims", "manage_contingent_orders"}
    user_params = [p for p in config_params if p["name"] not in internal_params]

    return {
        "name": name,
        "config_params": user_params,
        "optimize_ranges": optimize_ranges,
    }


class StrategyDefaults(BaseModel):
    """Default configuration for a strategy (from portfolio.yaml if applicable)."""

    symbols: list[str] = []
    interval: str | None = None
    starting_balance: float | None = None


@router.get("/{name}/defaults", response_model=StrategyDefaults)
async def get_strategy_defaults(
    name: str,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> StrategyDefaults:
    """Get default run configuration for a strategy.

    For portfolio strategies, returns symbols, interval, and starting_balance
    from portfolio.yaml. For single-file strategies, returns empty defaults.
    """
    stmt = select(Strategy).where(Strategy.name == name)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")

    file_path = Path(row.file_path)
    if file_path.name == "portfolio.yaml" and file_path.exists():
        import yaml

        with open(file_path) as f:
            raw = yaml.safe_load(f) or {}
        symbols = raw.get("symbols", [])
        interval = raw.get("interval")
        account = raw.get("account", {})
        starting_balance = account.get("starting_balance")
        return StrategyDefaults(
            symbols=symbols if isinstance(symbols, list) else [],
            interval=str(interval) if interval else None,
            starting_balance=float(starting_balance) if starting_balance else None,
        )

    return StrategyDefaults()


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
