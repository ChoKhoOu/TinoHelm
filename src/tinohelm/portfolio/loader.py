"""Portfolio loader — creates strategy and actor instances from PortfolioConfig.

This is the single entry point for strategy/actor instantiation, shared by
BacktestRunner, Sandbox node, and Live node.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from tinohelm.portfolio.config import PortfolioConfig, ActorRef
from tinohelm.strategy.utils import get_config_field_names

logger = logging.getLogger(__name__)

# Default actors directory
_DEFAULT_ACTORS_DIR = Path.home() / ".tino" / "actors"


def create_strategies(config: PortfolioConfig) -> list[Any]:
    """Create one strategy instance per symbol from a PortfolioConfig.

    Each instance gets its own ``instrument_id`` and ``bar_type`` injected
    into the config params.

    Returns a list of instantiated Strategy objects (NT Strategy subclasses).
    """
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.data import BarType

    strategy_cls, config_cls = _import_strategy_classes(config)

    # Validate symbols against SYMBOL_PROFILES if the strategy defines them
    _warn_unrecognized_symbols(strategy_cls, config.symbols)

    # Determine accepted config fields
    config_fields = get_config_field_names(config_cls)

    strategies = []
    for i, symbol in enumerate(config.symbols):
        # Build per-symbol params
        nt_symbol = _normalize_symbol(symbol)
        bar_type_str = _make_bar_type_str(symbol, config.interval)

        per_symbol_params = dict(config.params)
        per_symbol_params["instrument_id"] = nt_symbol
        per_symbol_params["bar_type"] = bar_type_str

        # Unique order_id_tag per instance — required for multi-strategy portfolios
        if "order_id_tag" not in per_symbol_params:
            per_symbol_params["order_id_tag"] = f"{i:03d}"

        # Auto-exit on stop for safety (cancel orders + close positions)
        if "manage_stop" not in per_symbol_params:
            per_symbol_params["manage_stop"] = True

        # Filter to fields the config accepts
        if config_fields:
            filtered = {k: v for k, v in per_symbol_params.items() if k in config_fields}
        else:
            filtered = per_symbol_params

        # Convert string values to NT types
        if "instrument_id" in filtered and isinstance(filtered["instrument_id"], str):
            filtered["instrument_id"] = InstrumentId.from_str(filtered["instrument_id"])
        if "bar_type" in filtered and isinstance(filtered["bar_type"], str):
            filtered["bar_type"] = BarType.from_str(filtered["bar_type"])

        strategy_instance = strategy_cls(config=config_cls(**filtered))
        strategies.append(strategy_instance)
        logger.info("Created strategy instance: %s for %s", strategy_cls.__name__, symbol)

    return strategies


def create_actors(
    config: PortfolioConfig,
    *,
    actors_dir: str | Path | None = None,
) -> list[Any]:
    """Create actor instances from a PortfolioConfig's actor references.

    Actors are loaded from:
    - ``~/.tino/actors/<name>.py`` when ``ActorRef.name`` is set
    - Portfolio folder relative path when ``ActorRef.class_path`` is set
      (e.g. ``./custom_monitor:MyMonitor``)

    Returns a list of instantiated Actor objects. Returns empty list if no
    actors are configured.
    """
    if not config.actors:
        return []

    actors_dir = Path(actors_dir) if actors_dir else _DEFAULT_ACTORS_DIR
    results = []

    for actor_ref in config.actors:
        actor_instance = _load_single_actor(actor_ref, config, actors_dir)
        results.append(actor_instance)

    return results


def _load_single_actor(
    ref: ActorRef,
    config: PortfolioConfig,
    actors_dir: Path,
) -> Any:
    """Load and instantiate a single actor from an ActorRef."""
    if ref.name:
        # Load from global actors directory: ~/.tino/actors/<name>.py
        actor_file = actors_dir / f"{ref.name}.py"
        if not actor_file.exists():
            raise FileNotFoundError(
                f"Actor '{ref.name}' not found at {actor_file}. "
                f"Create the file or use 'class' to specify a local path."
            )
        actor_cls, actor_config_cls = _discover_actor_classes(actor_file)

    elif ref.class_path:
        # Load from portfolio folder: ./module:ClassName
        if ref.class_path.startswith("./"):
            # Relative to portfolio folder
            module_part, class_name = ref.class_path.rsplit(":", 1)
            relative = module_part.removeprefix("./")
            module_file = (config.source_path / (relative + ".py")).resolve()
            # Validate path stays within portfolio folder
            if not str(module_file).startswith(str(config.source_path.resolve())):
                raise ValueError(
                    f"Actor class_path '{ref.class_path}' resolves outside portfolio folder"
                )
        else:
            # Absolute-style module:ClassName
            module_part, class_name = ref.class_path.rsplit(":", 1)
            module_file = Path(module_part + ".py")

        if not module_file.exists():
            raise FileNotFoundError(f"Actor module not found: {module_file}")

        actor_cls, actor_config_cls = _discover_actor_classes(module_file, class_name)
    else:
        raise ValueError("ActorRef must have either 'name' or 'class' set")

    # Instantiate with params
    if actor_config_cls and ref.params:
        config_fields = get_config_field_names(actor_config_cls)
        filtered_params = {k: v for k, v in ref.params.items() if k in config_fields} if config_fields else ref.params
        actor_config_instance = actor_config_cls(**filtered_params)
        actor_instance = actor_cls(config=actor_config_instance)
    elif actor_config_cls:
        actor_instance = actor_cls(config=actor_config_cls())
    else:
        actor_instance = actor_cls()

    logger.info("Created actor instance: %s", actor_cls.__name__)
    return actor_instance


def _import_strategy_classes(config: PortfolioConfig) -> tuple[type, type]:
    """Import strategy class and config class from a PortfolioConfig."""
    strategy_module_path, strategy_class_name = config.strategy_class.rsplit(":", 1)
    config_module_path, config_class_name = config.config_class.rsplit(":", 1)

    # Determine the actual file to load from
    source_path = config.source_path
    if source_path is None:
        raise ValueError("PortfolioConfig.source_path is required for strategy loading")

    # Try to find the module file
    strategy_file = _resolve_module_file(strategy_module_path, source_path)

    mod = _load_module_from_file(strategy_file, strategy_module_path)
    strategy_cls = getattr(mod, strategy_class_name)
    config_cls = getattr(mod, config_class_name)

    return strategy_cls, config_cls


def _resolve_module_file(module_path: str, source_path: Path) -> Path:
    """Resolve a module path string to an actual .py file."""
    # Check if it's a file path directly
    p = Path(module_path)
    if p.suffix == ".py" and p.exists():
        return p

    # Try as module name in source directory
    module_file = source_path / f"{module_path}.py"
    if module_file.exists():
        return module_file

    # Try as absolute path
    if p.is_absolute() and p.exists():
        return p

    # Fall back: search in strategies dir and CWD
    for base in [source_path, Path.cwd(), Path("/app")]:
        candidate = base / f"{module_path}.py"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Cannot find module '{module_path}' in {source_path} or other search paths"
    )


def _load_module_from_file(file_path: Path, module_name: str) -> Any:
    """Load a Python module from a file path.

    The module is left in ``sys.modules`` because instantiated Strategy/Actor
    objects reference it via ``__module__``. The fresh-import deletion ensures
    code changes are picked up on next load.
    """
    parent = str(file_path.parent.resolve())
    added_to_path = False
    if parent not in sys.path:
        sys.path.insert(0, parent)
        added_to_path = True

    clean_name = f"_portfolio_load_{file_path.stem}"

    # Remove cached module for fresh import
    if clean_name in sys.modules:
        del sys.modules[clean_name]

    spec = importlib.util.spec_from_file_location(clean_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {file_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[clean_name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        # Clean up sys.path to avoid pollution in long-running processes
        if added_to_path:
            try:
                sys.path.remove(parent)
            except ValueError:
                pass
    return mod


def _discover_actor_classes(
    file_path: Path,
    class_name: str | None = None,
) -> tuple[type, type | None]:
    """Discover Actor and ActorConfig subclasses from a .py file.

    If ``class_name`` is given, look for that specific class.
    Otherwise, find the first Actor subclass defined in the module.
    """
    mod = _load_module_from_file(file_path, file_path.stem)

    actor_cls = None
    config_cls = None

    for name, obj in inspect.getmembers(mod, inspect.isclass):
        if obj.__module__ != mod.__name__:
            continue

        if class_name and name == class_name:
            actor_cls = obj
            continue

        for base in inspect.getmro(obj):
            if base.__name__ == "Actor" and base.__module__.startswith("nautilus_trader"):
                if class_name is None or name == class_name:
                    actor_cls = obj
            if base.__name__ == "ActorConfig" and base.__module__.startswith("nautilus_trader"):
                config_cls = obj

    if actor_cls is None:
        raise ValueError(
            f"No Actor subclass{' named ' + class_name if class_name else ''} "
            f"found in {file_path}"
        )

    return actor_cls, config_cls


def scan_actors(actors_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """Scan the actors directory for Actor/ActorConfig subclasses.

    Returns a list of dicts with actor metadata for each discovered .py file.
    """
    actors_dir = Path(actors_dir) if actors_dir else _DEFAULT_ACTORS_DIR
    if not actors_dir.exists():
        return []

    results = []
    for py_file in sorted(actors_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            actor_cls, config_cls = _discover_actor_classes(py_file)
            results.append({
                "name": py_file.stem,
                "file_path": str(py_file),
                "actor_class": actor_cls.__name__,
                "config_class": config_cls.__name__ if config_cls else None,
            })
            logger.info("Discovered actor: %s (%s)", py_file.stem, actor_cls.__name__)
        except Exception as e:
            logger.warning("Failed to scan actor %s: %s", py_file.name, e)

    return results


def _warn_unrecognized_symbols(strategy_cls: type, symbols: list[str]) -> None:
    """Warn if symbols have no matching SYMBOL_PROFILES entry in the strategy.

    Looks for a module-level ``SYMBOL_PROFILES`` dict and ``DEFAULT_PROFILE``
    in the strategy's module. If a symbol (converted to Jesse format like
    ``BTC-USDT``) has no entry or has ``enabled: False``, logs a warning.
    """
    mod = sys.modules.get(strategy_cls.__module__)
    if mod is None:
        return

    profiles = getattr(mod, "SYMBOL_PROFILES", None)
    default_profile = getattr(mod, "DEFAULT_PROFILE", None)
    if profiles is None:
        return  # Strategy doesn't define SYMBOL_PROFILES, skip validation

    for symbol in symbols:
        # Convert NT symbol to Jesse format: BTCUSDT-PERP -> BTC-USDT
        jesse_sym = _nt_symbol_to_jesse(symbol)
        profile = profiles.get(jesse_sym)

        if profile is None:
            logger.warning(
                "Symbol '%s' (jesse: '%s') has no entry in SYMBOL_PROFILES. "
                "Will use DEFAULT_PROFILE and may not generate trading signals.",
                symbol, jesse_sym,
            )
        elif not profile.get("enabled", True):
            logger.warning(
                "Symbol '%s' (jesse: '%s') has enabled=False in SYMBOL_PROFILES. "
                "Strategy may not generate signals for this symbol.",
                symbol, jesse_sym,
            )


def _nt_symbol_to_jesse(symbol: str) -> str:
    """Convert NT-style symbol to Jesse format: BTCUSDT-PERP -> BTC-USDT."""
    raw = symbol.replace(".BINANCE", "")
    for suffix in ("-PERP", "-SWAP", "-SPOT", "-LINEAR"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    for quote in ("USDT", "USDC", "BUSD", "USD", "BTC", "ETH"):
        if raw.endswith(quote) and len(raw) > len(quote):
            base = raw[: -len(quote)]
            return f"{base}-{quote}"
    return raw


# --- Shared helpers (reused from runner.py to maintain single implementation) ---

_INTERVAL_MAP = {
    "1m": "1-MINUTE", "3m": "3-MINUTE", "5m": "5-MINUTE",
    "15m": "15-MINUTE", "30m": "30-MINUTE",
    "1h": "1-HOUR", "2h": "2-HOUR", "4h": "4-HOUR",
    "6h": "6-HOUR", "8h": "8-HOUR", "12h": "12-HOUR",
    "1d": "1-DAY",
}


def _normalize_symbol(symbol: str) -> str:
    """Normalize symbol to NT format: BTCUSDT-PERP -> BTCUSDT-PERP.BINANCE"""
    s = symbol.replace(".BINANCE", "")
    return f"{s}.BINANCE"


def _make_bar_type_str(symbol: str, interval: str) -> str:
    """Build NT bar type string: BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"""
    nt_symbol = _normalize_symbol(symbol)
    interval_part = _INTERVAL_MAP.get(interval, "1-MINUTE")
    return f"{nt_symbol}-{interval_part}-LAST-EXTERNAL"
