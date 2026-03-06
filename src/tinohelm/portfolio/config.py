"""Portfolio configuration — schema and loader for portfolio.yaml."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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
class PortfolioConfig:
    """Parsed portfolio configuration.

    Can be loaded from a ``portfolio.yaml`` file or auto-wrapped from
    a single ``.py`` strategy file with CLI arguments.
    """

    # Strategy references
    strategy_class: str  # "module:ClassName" or "strategy:ClassName"
    config_class: str  # "module:ConfigClassName"

    # Symbols and interval
    symbols: list[str]
    interval: str

    # Strategy params (passed to config constructor)
    params: dict[str, Any] = field(default_factory=dict)

    # Actors (optional)
    actors: list[ActorRef] = field(default_factory=list)

    # Account settings
    account: AccountSettings = field(default_factory=AccountSettings)

    # Source path (folder or .py file)
    source_path: Path | None = None

    # Whether this was auto-wrapped from a single .py file
    implicit: bool = False


def load_portfolio_config(
    name_or_path: str,
    *,
    strategies_dir: str | Path | None = None,
    symbol: str | None = None,
    interval: str | None = None,
    strategy_params: dict[str, Any] | None = None,
) -> PortfolioConfig:
    """Load a PortfolioConfig from a name or path.

    Resolution order:
    1. If ``name_or_path`` points to a folder with ``portfolio.yaml``, load it.
    2. If ``~/.tino/strategies/<name>/portfolio.yaml`` exists, load it.
    3. If ``~/.tino/strategies/<name>.py`` exists, auto-wrap as implicit portfolio.
    4. If ``name_or_path`` is a ``.py`` file path, auto-wrap as implicit portfolio.

    For implicit portfolios (single .py files), ``symbol`` and ``interval``
    CLI arguments are required.
    """
    strategies_dir = Path(strategies_dir) if strategies_dir else _DEFAULT_STRATEGIES_DIR
    path = Path(name_or_path)

    # Case 1: direct folder path with portfolio.yaml
    if path.is_dir() and (path / "portfolio.yaml").exists():
        return _load_from_yaml(path / "portfolio.yaml", path)

    # Case 2: name resolves to a portfolio folder
    portfolio_dir = strategies_dir / name_or_path
    if portfolio_dir.is_dir() and (portfolio_dir / "portfolio.yaml").exists():
        return _load_from_yaml(portfolio_dir / "portfolio.yaml", portfolio_dir)

    # Case 3: name resolves to a single .py file
    py_file = strategies_dir / f"{name_or_path}.py"
    if py_file.exists():
        return _wrap_single_file(py_file, symbol=symbol, interval=interval, params=strategy_params)

    # Case 4: direct .py file path
    if path.suffix == ".py" and path.exists():
        return _wrap_single_file(path, symbol=symbol, interval=interval, params=strategy_params)

    raise FileNotFoundError(
        f"Cannot find portfolio or strategy: '{name_or_path}'. "
        f"Looked in: {portfolio_dir}, {py_file}, {path}"
    )


def _load_from_yaml(yaml_path: Path, folder: Path) -> PortfolioConfig:
    """Parse a portfolio.yaml file into a PortfolioConfig."""
    with open(yaml_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"portfolio.yaml must be a YAML mapping, got {type(raw).__name__}")

    # Validate required fields
    strategy_section = raw.get("strategy")
    if not strategy_section or not isinstance(strategy_section, dict):
        raise ValueError("portfolio.yaml: missing required 'strategy' section")

    strategy_class = strategy_section.get("class")
    if not strategy_class:
        raise ValueError("portfolio.yaml: missing required 'strategy.class' field")

    # Config class defaults to strategy class name + "Config"
    config_class = strategy_section.get("config")
    if not config_class:
        # Derive from strategy class: "module:Foo" -> "module:FooConfig"
        config_class = strategy_class + "Config"

    symbols = raw.get("symbols", [])
    if not symbols:
        raise ValueError("portfolio.yaml: missing required 'symbols' list")
    if not isinstance(symbols, list):
        raise ValueError(f"portfolio.yaml: 'symbols' must be a list, got {type(symbols).__name__}")

    interval = raw.get("interval")
    if not interval:
        raise ValueError("portfolio.yaml: missing required 'interval' field")

    # Validate symbol format
    for sym in symbols:
        if not _SYMBOL_RE.match(sym):
            logger.warning(
                "Symbol '%s' does not match expected format (e.g. BTCUSDT-PERP). "
                "Proceeding anyway.",
                sym,
            )

    # Parse actors
    actors = []
    for actor_raw in raw.get("actors", []) or []:
        if isinstance(actor_raw, dict):
            actors.append(ActorRef(
                name=actor_raw.get("name"),
                class_path=actor_raw.get("class"),
                params=actor_raw.get("params", {}),
            ))

    # Parse account settings
    account_raw = raw.get("account", {})
    account = AccountSettings(
        starting_balance=account_raw.get("starting_balance", 10000),
        currency=account_raw.get("currency", "USDT"),
        leverage=account_raw.get("leverage", 1),
    )

    # Strategy params
    params = raw.get("params", {}) or {}

    config = PortfolioConfig(
        strategy_class=strategy_class,
        config_class=config_class,
        symbols=symbols,
        interval=interval,
        params=params,
        actors=actors,
        account=account,
        source_path=folder,
        implicit=False,
    )

    logger.info(
        "Loaded portfolio config: %d symbols, %d actors, from %s",
        len(symbols), len(actors), yaml_path,
    )
    return config


def _wrap_single_file(
    py_file: Path,
    *,
    symbol: str | None = None,
    interval: str | None = None,
    params: dict[str, Any] | None = None,
) -> PortfolioConfig:
    """Auto-wrap a single .py strategy file as an implicit PortfolioConfig."""
    if not symbol:
        raise ValueError(
            f"Single-file strategy '{py_file.name}' requires --symbol argument"
        )
    if not interval:
        raise ValueError(
            f"Single-file strategy '{py_file.name}' requires --interval argument"
        )

    # Discover strategy and config class names from the file
    strategy_class_name, config_class_name = _discover_classes(py_file)

    module_stem = py_file.stem
    strategy_class = f"{module_stem}:{strategy_class_name}"
    config_class = f"{module_stem}:{config_class_name}"

    symbols = [symbol] if isinstance(symbol, str) else symbol

    return PortfolioConfig(
        strategy_class=strategy_class,
        config_class=config_class,
        symbols=symbols,
        interval=interval,
        params=params or {},
        actors=[],
        account=AccountSettings(),
        source_path=py_file.parent,
        implicit=True,
    )


def _discover_classes(py_file: Path) -> tuple[str, str]:
    """Discover Strategy and StrategyConfig class names from a .py file.

    Uses the same import mechanism as the scanner to find NT subclasses.
    """
    import importlib.util
    import inspect
    import sys

    module_name = f"_portfolio_discover_{py_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, str(py_file))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {py_file}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        raise ImportError(f"Failed to import {py_file}: {e}") from e

    strategy_cls_name = None
    config_cls_name = None

    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if obj.__module__ != module_name:
            continue
        for base in inspect.getmro(obj):
            if base.__name__ == "Strategy" and base.__module__.startswith("nautilus_trader"):
                strategy_cls_name = name
            if base.__name__ == "StrategyConfig" and base.__module__.startswith("nautilus_trader"):
                config_cls_name = name

    if not strategy_cls_name:
        raise ValueError(f"No Strategy subclass found in {py_file}")
    if not config_cls_name:
        raise ValueError(f"No StrategyConfig subclass found in {py_file}")

    # Clean up
    del sys.modules[module_name]

    return strategy_cls_name, config_cls_name
