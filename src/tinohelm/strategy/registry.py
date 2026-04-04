"""Strategy registry — discovers and tracks strategy files."""
from __future__ import annotations

import hashlib
import inspect
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tinohelm.db.models import Strategy, StrategyVersion
from tinohelm.strategy.module_loader import load_module_from_file
from tinohelm.strategy.utils import get_config_fields

logger = logging.getLogger(__name__)


def _compute_hash(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def scan_strategies(strategies_dir: str | Path) -> list[dict[str, Any]]:
    """Scan directory for strategy .py files.

    Uses the unified module_loader for file discovery and validation,
    then extracts full metadata for each valid strategy.

    Returns list of dicts with strategy metadata.
    """
    from tinohelm.strategy.module_loader import scan_valid_strategy_files

    valid_files = scan_valid_strategy_files(strategies_dir)
    results = []

    for name, py_file in valid_files.items():
        try:
            info = _scan_single_file(py_file)
            if info:
                results.append(info)
        except Exception as e:
            logger.warning(f"Failed to scan {py_file.name}: {e}")

    return results


def _scan_single_file(py_file: Path) -> dict[str, Any] | None:
    """Scan a single .py file for Strategy/StrategyConfig subclasses."""
    module = load_module_from_file(py_file)

    strategy_cls = None
    config_cls = None

    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        for base in inspect.getmro(obj):
            if base.__name__ == "Strategy" and base.__module__.startswith("nautilus_trader"):
                strategy_cls = obj
            if base.__name__ == "StrategyConfig" and base.__module__.startswith("nautilus_trader"):
                config_cls = obj

    if not (strategy_cls and config_cls):
        return None

    module_name = py_file.stem

    config_params = get_config_fields(config_cls)
    from tinohelm.strategy.utils import parse_optimize_ranges
    optimize_ranges = parse_optimize_ranges(getattr(module, "OPTIMIZE", {}) or {})

    hooks = []
    for hook_name in [
        "on_start", "on_stop", "on_bar", "on_quote_tick", "on_trade_tick",
        "on_order_book", "on_order_filled", "on_position_opened",
        "on_position_changed", "on_position_closed", "on_event",
    ]:
        if hook_name in strategy_cls.__dict__:
            hooks.append(hook_name)

    logger.info(f"Discovered strategy: {module_name} ({strategy_cls.__name__})")
    return {
        "name": module_name,
        "file_path": str(py_file),
        "strategy_class": strategy_cls.__name__,
        "config_class": config_cls.__name__,
        "module_path": f"{module_name}:{strategy_cls.__name__}",
        "config_module_path": f"{module_name}:{config_cls.__name__}",
        "code_hash": _compute_hash(py_file),
        "config_params": config_params,
        "hooks": hooks,
        "type": "single",
        "optimize_ranges": optimize_ranges,
    }


async def persist_strategies(
    db: AsyncSession, discovered: list[dict[str, Any]], *, rebuild: bool = False,
) -> None:
    """Persist discovered strategies to the database.

    Args:
        db: Async database session.
        discovered: List of strategy metadata dicts from ``scan_strategies()``.
        rebuild: If True, delete all existing strategies and rebuild from
            scratch. The ``backtest_runs`` table uses ``strategy_name``
            (not FK), so it is unaffected by rebuilds.
    """
    if rebuild:
        # Full rebuild: clear strategies and versions, then re-insert.
        # Must nullify backtest_runs FK first to avoid constraint violations.
        from sqlalchemy import delete, update
        from tinohelm.db.models import BacktestRun
        await db.execute(
            update(BacktestRun)
            .where(BacktestRun.strategy_version_id.isnot(None))
            .values(strategy_version_id=None)
        )
        await db.execute(delete(StrategyVersion))
        await db.execute(delete(Strategy))
        await db.flush()

    for info in discovered:
        name = info["name"]
        code_hash = info["code_hash"]
        strategy_type = info.get("type", "single")

        # Upsert Strategy record
        result = await db.execute(
            select(Strategy).where(Strategy.name == name)
        )
        strategy = result.scalar_one_or_none()

        if strategy is None:
            strategy = Strategy(
                name=name,
                file_path=info["file_path"],
                strategy_class=info["strategy_class"],
                config_class=info["config_class"],
                type=strategy_type,
            )
            db.add(strategy)
            await db.flush()  # populate strategy.id
        else:
            strategy.file_path = info["file_path"]
            strategy.strategy_class = info["strategy_class"]
            strategy.config_class = info["config_class"]
            strategy.type = strategy_type

        # Check latest StrategyVersion
        ver_result = await db.execute(
            select(StrategyVersion)
            .where(StrategyVersion.strategy_id == strategy.id)
            .order_by(StrategyVersion.version.desc())
            .limit(1)
        )
        latest_version = ver_result.scalar_one_or_none()

        if latest_version is None or latest_version.code_hash != code_hash:
            next_ver = (latest_version.version + 1) if latest_version else 1
            db.add(StrategyVersion(
                strategy_id=strategy.id,
                version=next_ver,
                code_hash=code_hash,
            ))

    await db.commit()
    logger.info("Persisted %d strategies to database (rebuild=%s)", len(discovered), rebuild)
