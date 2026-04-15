"""Strategy bundle configuration — schema and loader for strategy .py files."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Matches symbols like BTCUSDT-PERP, ETHUSDT-PERP, SOLUSDT-PERP
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+-[A-Z]+$")

# Default strategies directory
_DEFAULT_STRATEGIES_DIR = Path.home() / ".tino" / "strategies"


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
    1. If ``~/.tino/strategies/<name>/portfolio.yaml`` exists, load portfolio.
    2. If ``~/.tino/strategies/<name>.py`` exists, wrap it.
    3. If ``name_or_path`` is a ``.py`` file path, wrap it.

    ``symbol``/``symbols`` and ``interval`` are required for single .py files
    but are read from ``portfolio.yaml`` for portfolio folders.
    """
    strategies_dir = Path(strategies_dir) if strategies_dir else _DEFAULT_STRATEGIES_DIR
    path = Path(name_or_path)

    # Merge symbol/symbols for backward compat
    effective_symbols = symbols or ([symbol] if symbol else None)

    # Case 0: portfolio folder with portfolio.yaml
    portfolio_dir = strategies_dir / name_or_path
    yaml_path = portfolio_dir / "portfolio.yaml"
    if yaml_path.exists():
        return _load_portfolio_folder(portfolio_dir, yaml_path, params=strategy_params)

    # Case 1: name resolves to a single .py file
    py_file = strategies_dir / f"{name_or_path}.py"
    if py_file.exists():
        return _wrap_single_file(py_file, symbols=effective_symbols, interval=interval, params=strategy_params)

    # Case 2: direct .py file path
    if path.suffix == ".py" and path.exists():
        return _wrap_single_file(path, symbols=effective_symbols, interval=interval, params=strategy_params)

    raise FileNotFoundError(
        f"Cannot find strategy: '{name_or_path}'. "
        f"Looked in: {portfolio_dir}, {py_file}, {path}"
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


def _load_portfolio_folder(
    portfolio_dir: Path,
    yaml_path: Path,
    *,
    params: dict[str, Any] | None = None,
) -> StrategyBundle:
    """Load a StrategyBundle from a portfolio folder with portfolio.yaml."""
    import yaml as _yaml

    with open(yaml_path) as f:
        cfg = _yaml.safe_load(f) or {}

    symbols = cfg.get("symbols")
    if not symbols:
        raise ValueError(
            f"portfolio.yaml in '{portfolio_dir.name}' requires 'symbols' field"
        )
    interval = cfg.get("interval")
    if not interval:
        raise ValueError(
            f"portfolio.yaml in '{portfolio_dir.name}' requires 'interval' field"
        )

    # Discover strategy class from strategy.py in the folder
    strategy_py = portfolio_dir / "strategy.py"
    if not strategy_py.exists():
        raise FileNotFoundError(
            f"Portfolio folder '{portfolio_dir.name}' missing strategy.py"
        )

    strategy_class_name, config_class_name, optimize_ranges = _discover_classes(strategy_py)
    module_stem = strategy_py.stem
    strategy_class = f"{module_stem}:{strategy_class_name}"
    config_class = f"{module_stem}:{config_class_name}"

    # Merge params: portfolio.yaml params < runtime overrides
    merged_params = cfg.get("params", {})
    if params:
        merged_params.update(params)

    # Parse actors
    actors = []
    for actor_cfg in cfg.get("actors", []):
        actors.append(ActorRef(
            name=actor_cfg.get("name"),
            class_path=actor_cfg.get("class_path"),
            params=actor_cfg.get("params", {}),
        ))

    # Parse account settings
    account_cfg = cfg.get("account", {})
    account = AccountSettings(
        starting_balance=account_cfg.get("starting_balance", 10000),
        currency=account_cfg.get("currency", "USDT"),
        leverage=account_cfg.get("leverage", 1),
    )

    # Parse risk guard settings
    risk_guard = None
    rg_cfg = cfg.get("risk_guard")
    if rg_cfg and rg_cfg.get("enabled", True):
        risk_guard = RiskGuardSettings(**{
            k: v for k, v in rg_cfg.items()
            if k in RiskGuardSettings.__dataclass_fields__
        })

    return StrategyBundle(
        strategy_class=strategy_class,
        config_class=config_class,
        symbols=symbols,
        interval=interval,
        params=merged_params,
        actors=actors,
        account=account,
        risk_guard=risk_guard,
        optimize_ranges=optimize_ranges,
        source_path=portfolio_dir,
        tag=cfg.get("tag"),
        implicit=False,
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
