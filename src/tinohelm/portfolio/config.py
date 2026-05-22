"""Strategy bundle configuration — schema and loader for strategy .py files."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tinohelm.core.paths import paths

logger = logging.getLogger(__name__)

# Matches symbols like BTCUSDT-PERP, ETHUSDT-PERP, SOLUSDT-PERP
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+-[A-Z]+$")


@dataclass
class ActorRef:
    """Reference to an Actor with optional params."""

    name: str | None = None
    class_path: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountSettings:
    """Account settings for the portfolio."""

    starting_balance: float = 10000
    currency: str = "USDT"
    leverage: int = 1


@dataclass
class RiskGuardSettings:
    """Declarative risk guard configuration."""

    enabled: bool = True
    check_interval_secs: int = 10
    daily_stop_loss_pct: float | None = None
    max_drawdown_pct: float | None = None
    max_total_exposure: float | None = None
    max_positions: int | None = None
    breach_action: str = "reduce_only"


@dataclass
class StrategyBundle:
    """Internal configuration bundle for strategy loading.

    Holds everything the loader/runner/node needs to instantiate a strategy.
    Loaded from a single ``.py`` strategy file with CLI arguments.
    """

    # Strategy references
    strategy_class: str  # "module:ClassName" or "strategy:ClassName"
    config_class: str  # "module:ConfigClassName"

    # Symbols and interval
    symbols: list[str]
    interval: str

    # Strategy params (passed to config constructor)
    params: dict[str, Any] = field(default_factory=dict)

    # Resolved bar types (injected by runner for composite aggregation)
    resolved_bar_types: list[str] = field(default_factory=list)

    # Actors (optional)
    actors: list[ActorRef] = field(default_factory=list)

    # Account settings
    account: AccountSettings = field(default_factory=AccountSettings)

    # Declarative risk guard settings (optional)
    risk_guard: RiskGuardSettings | None = None

    # Manual tag override for StrategyRegistry
    tag: str | None = None

    # Optimization parameter ranges (optional)
    optimize_ranges: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Source path (.py file parent dir)
    source_path: Path | None = None

    # Whether this was auto-wrapped from a single .py file
    implicit: bool = False




def load_strategy_bundle(
    name_or_path: str,
    *,
    strategies_dir: str | Path | None = None,
    symbol: str | None = None,
    symbols: list[str] | None = None,
    interval: str | None = None,
    strategy_params: dict[str, Any] | None = None,
) -> StrategyBundle:
    """Load a StrategyBundle from a strategy name or .py file path.

    Resolution order:
    1. If ``~/.tino/strategies/<name>.py`` exists, wrap it.
    2. If ``name_or_path`` is a ``.py`` file path, wrap it.

    ``symbol``/``symbols`` and ``interval`` are required.
    """
    strategies_dir = (
        Path(strategies_dir) if strategies_dir else paths.get("strategies")
    )
    path = Path(name_or_path)

    # Merge symbol/symbols for backward compat
    effective_symbols = symbols or ([symbol] if symbol else None)

    # Case 1: name resolves to a single .py file
    py_file = strategies_dir / f"{name_or_path}.py"
    if py_file.exists():
        return _wrap_single_file(py_file, symbols=effective_symbols, interval=interval, params=strategy_params)

    # Case 2: direct .py file path
    if path.suffix == ".py" and path.exists():
        return _wrap_single_file(path, symbols=effective_symbols, interval=interval, params=strategy_params)

    raise FileNotFoundError(
        f"Cannot find strategy: '{name_or_path}'. "
        f"Looked in: {py_file}, {path}"
    )


def _wrap_single_file(
    py_file: Path,
    *,
    symbol: str | None = None,
    symbols: list[str] | None = None,
    interval: str | None = None,
    params: dict[str, Any] | None = None,
) -> StrategyBundle:
    """Wrap a single .py strategy file as a StrategyBundle."""
    # Merge symbol/symbols
    effective_symbols = symbols or ([symbol] if symbol else None)
    if not effective_symbols:
        raise ValueError(
            f"Strategy '{py_file.name}' requires --symbol or --symbols argument"
        )
    if not interval:
        raise ValueError(
            f"Strategy '{py_file.name}' requires --interval argument"
        )

    # Discover strategy and config class names from the file
    strategy_class_name, config_class_name, optimize_ranges = _discover_classes(py_file)

    module_stem = py_file.stem
    strategy_class = f"{module_stem}:{strategy_class_name}"
    config_class = f"{module_stem}:{config_class_name}"

    return StrategyBundle(
        strategy_class=strategy_class,
        config_class=config_class,
        symbols=effective_symbols,
        interval=interval,
        params=params or {},
        actors=[],
        account=AccountSettings(),
        optimize_ranges=optimize_ranges,
        source_path=py_file.parent,
        implicit=True,
    )


def _discover_classes(py_file: Path) -> tuple[str, str, dict[str, dict[str, Any]]]:
    """Discover Strategy and StrategyConfig class names from a .py file.

    Returns (strategy_class_name, config_class_name, optimize_ranges).
    """
    from tinohelm.strategy.module_loader import load_strategy_module

    result = load_strategy_module(py_file)
    if result.strategy_cls is None:
        raise ValueError(f"No Strategy subclass found in {py_file}")
    if result.config_cls is None:
        raise ValueError(f"No StrategyConfig subclass found in {py_file}")

    return result.strategy_cls.__name__, result.config_cls.__name__, result.optimize_ranges
