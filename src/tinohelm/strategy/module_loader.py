"""Unified strategy/actor module loader.

Single entry point for all dynamic module loading from .py files.
Replaces 6 separate importlib patterns across the codebase.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModuleLoadResult:
    """Result of loading a Python module from a file."""

    module: Any
    strategy_cls: type | None = None
    config_cls: type | None = None
    actor_cls: type | None = None
    actor_config_cls: type | None = None
    optimize_ranges: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None


def load_module_from_file(
    file_path: Path,
    module_name: str | None = None,
    *,
    boundary_dir: Path | None = None,
) -> Any:
    """Load a Python module from a file path.

    Args:
        file_path: Path to the .py file.
        module_name: Optional custom module name. Auto-generated if None.
        boundary_dir: If set, verify resolved path stays within this directory.

    Returns:
        The loaded module.

    Raises:
        FileNotFoundError: If the file does not exist.
        ImportError: If the module cannot be loaded.
        ValueError: If the file is outside the boundary directory.
    """
    file_path = Path(file_path).resolve()

    if not file_path.exists():
        raise FileNotFoundError(f"Module file not found: {file_path}")

    if boundary_dir is not None:
        boundary = Path(boundary_dir).resolve()
        if not str(file_path).startswith(str(boundary)):
            raise ValueError(
                f"Path {file_path} is outside boundary {boundary}"
            )

    if module_name is None:
        h = hashlib.md5(str(file_path).encode()).hexdigest()[:8]
        module_name = f"_tino_load_{file_path.stem}_{h}"

    # Clean up cached module for fresh import
    sys.modules.pop(module_name, None)

    parent_dir = str(file_path.parent)
    added_to_path = parent_dir not in sys.path

    if added_to_path:
        sys.path.insert(0, parent_dir)

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec from {file_path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        # Clean up on failure
        sys.modules.pop(module_name, None)
        raise
    finally:
        if added_to_path:
            try:
                sys.path.remove(parent_dir)
            except ValueError:
                pass


def discover_strategy_classes(
    module: Any,
) -> tuple[type | None, type | None]:
    """Discover Strategy and StrategyConfig subclasses in a module.

    Returns:
        (strategy_cls, config_cls) — either may be None if not found.
    """
    strategy_cls = None
    config_cls = None

    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        for base in inspect.getmro(obj):
            if base.__name__ == "Strategy" and base.__module__.startswith(
                "nautilus_trader"
            ):
                strategy_cls = obj
                break
            if base.__name__ == "StrategyConfig" and base.__module__.startswith(
                "nautilus_trader"
            ):
                config_cls = obj
                break

    return strategy_cls, config_cls


def discover_actor_classes(
    module: Any,
    class_name: str | None = None,
) -> tuple[type | None, type | None]:
    """Discover Actor and ActorConfig subclasses in a module.

    Args:
        module: The loaded module to inspect.
        class_name: If set, only match this specific class name.

    Returns:
        (actor_cls, actor_config_cls) — either may be None if not found.
    """
    actor_cls = None
    config_cls = None

    for name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue

        if class_name and name == class_name:
            actor_cls = obj
            continue

        for base in inspect.getmro(obj):
            if base.__name__ == "Actor" and base.__module__.startswith(
                "nautilus_trader"
            ):
                if class_name is None or name == class_name:
                    actor_cls = obj
                break
            if base.__name__ == "ActorConfig" and base.__module__.startswith(
                "nautilus_trader"
            ):
                config_cls = obj
                break

    return actor_cls, config_cls


def scan_valid_strategy_files(strategies_dir: Path) -> dict[str, Path]:
    """Scan a directory for valid NT strategy .py files.

    Validates each file by importing and checking for Strategy/StrategyConfig
    subclasses. Files that fail import or lack NT classes are skipped.

    Returns:
        Dict of {strategy_name: file_path} for valid strategies.
    """
    strategies_dir = Path(strategies_dir)
    if not strategies_dir.exists():
        logger.warning("Strategies directory not found: %s", strategies_dir)
        return {}

    valid: dict[str, Path] = {}
    for item in sorted(strategies_dir.iterdir()):
        # Portfolio-folder strategy: directory with portfolio.yaml + strategy.py
        if item.is_dir() and not item.name.startswith("_"):
            strategy_py = item / "strategy.py"
            portfolio_yaml = item / "portfolio.yaml"
            if portfolio_yaml.exists() and strategy_py.exists():
                try:
                    mod = load_module_from_file(strategy_py)
                    strategy_cls, config_cls = discover_strategy_classes(mod)
                    if strategy_cls is not None and config_cls is not None:
                        valid[item.name] = strategy_py
                    else:
                        logger.debug("Skipped portfolio %s: no NT Strategy subclass", item.name)
                except Exception as e:
                    logger.debug("Skipped portfolio %s: %s", item.name, e)
            continue

        # Single-file strategy
        if not (item.is_file() and item.suffix == ".py") or item.name.startswith("_"):
            continue
        try:
            mod = load_module_from_file(item)
            strategy_cls, config_cls = discover_strategy_classes(mod)
            if strategy_cls is not None and config_cls is not None:
                valid[item.stem] = item
            else:
                logger.debug("Skipped %s: no NT Strategy subclass", item.name)
        except Exception as e:
            logger.debug("Skipped %s: %s", item.name, e)

    return valid


def load_strategy_module(
    file_path: Path,
    *,
    boundary_dir: Path | None = None,
) -> ModuleLoadResult:
    """Load a strategy module and discover its classes.

    This is the high-level convenience function that combines
    load_module_from_file + discover_strategy_classes.

    Returns:
        ModuleLoadResult with module, strategy_cls, config_cls, and optimize_ranges.
    """
    from tinohelm.strategy.utils import parse_optimize_ranges

    mod = load_module_from_file(file_path, boundary_dir=boundary_dir)
    strategy_cls, config_cls = discover_strategy_classes(mod)

    optimize_ranges = {}
    raw_optimize = getattr(mod, "OPTIMIZE", None)
    if raw_optimize:
        optimize_ranges = parse_optimize_ranges(raw_optimize)

    return ModuleLoadResult(
        module=mod,
        strategy_cls=strategy_cls,
        config_cls=config_cls,
        optimize_ranges=optimize_ranges,
        path=file_path,
    )
