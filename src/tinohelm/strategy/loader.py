"""Strategy loader — creates strategy and actor instances from StrategyBundle.

This is the single entry point for strategy/actor instantiation, shared by
BacktestRunner, Sandbox node, and Live node.
"""
from __future__ import annotations

import inspect
import logging
import sys
from pathlib import Path
from typing import Any

from tinohelm.portfolio.config import StrategyBundle, ActorRef
from tinohelm.strategy.module_loader import load_module_from_file
from tinohelm.strategy.utils import get_config_field_names

logger = logging.getLogger(__name__)

# Default actors directory
_DEFAULT_ACTORS_DIR = Path.home() / ".tino" / "actors"
# Built-in actors shipped with the package
_BUILTIN_ACTORS_DIR = Path(__file__).resolve().parent.parent / "actors"


# --- Public helpers (used by scaffold templates, runner, etc.) ---

INTERVAL_MAP = {
    "1m": "1-MINUTE", "3m": "3-MINUTE", "5m": "5-MINUTE",
    "15m": "15-MINUTE", "30m": "30-MINUTE",
    "1h": "1-HOUR", "2h": "2-HOUR", "4h": "4-HOUR",
    "6h": "6-HOUR", "8h": "8-HOUR", "12h": "12-HOUR",
    "1d": "1-DAY",
}

_UNIT_MAP = {"s": "SECOND", "m": "MINUTE", "h": "HOUR", "d": "DAY"}


def parse_interval(interval: str) -> str:
    """Parse interval string to NT format: '5m' -> '5-MINUTE', '2h' -> '2-HOUR'.

    Supports arbitrary positive integers with s/m/h/d units.
    Falls back to INTERVAL_MAP for known values, then dynamic parsing.
    """
    if interval in INTERVAL_MAP:
        return INTERVAL_MAP[interval]
    import re
    m = re.match(r"^(\d+)([smhd])$", interval.lower())
    if m:
        return f"{m.group(1)}-{_UNIT_MAP[m.group(2)]}"
    return "1-MINUTE"


def normalize_symbol(symbol: str) -> str:
    """Normalize symbol to NT format: BTCUSDT-PERP -> BTCUSDT-PERP.BINANCE"""
    s = symbol.replace(".BINANCE", "")
    return f"{s}.BINANCE"


def make_bar_type_str(symbol: str, interval: str) -> str:
    """Build NT bar type string: BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL"""
    nt_symbol = normalize_symbol(symbol)
    interval_part = parse_interval(interval)
    return f"{nt_symbol}-{interval_part}-LAST-EXTERNAL"


def create_strategies(
    config: StrategyBundle,
    *,
    order_id_tag: str | None = None,
    # Backward-compat: accept old list-based signature
    order_id_tags: list[str] | None = None,
) -> list[Any]:
    """Create a single strategy instance from a StrategyBundle.

    The strategy receives ``symbols``, ``interval``, and ``resolved_bar_types``
    in its config. For backward compatibility, if the strategy's config class
    still declares ``instrument_id`` / ``bar_type`` fields, the first symbol's
    values are injected.

    Returns a list containing one instantiated Strategy object.
    """
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.data import BarType

    strategy_cls, config_cls = _import_strategy_classes(config)

    # Validate symbols against SYMBOL_PROFILES if the strategy defines them
    _warn_unrecognized_symbols(strategy_cls, config.symbols)

    # Determine accepted config fields
    config_fields = get_config_field_names(config_cls)

    params = dict(config.params)

    # New fields: symbols, interval, resolved_bar_types
    params["symbols"] = config.symbols
    params["interval"] = config.interval
    if config.resolved_bar_types:
        params["resolved_bar_types"] = config.resolved_bar_types

    # Backward compat: inject instrument_id/bar_type for old strategies
    if config_fields and "instrument_id" in config_fields and config.symbols:
        params["instrument_id"] = normalize_symbol(config.symbols[0])
    if config_fields and "bar_type" in config_fields and config.symbols and config.interval:
        if config.resolved_bar_types:
            params["bar_type"] = config.resolved_bar_types[0]
        else:
            params["bar_type"] = make_bar_type_str(config.symbols[0], config.interval)

    # Tag: prefer explicit arg, then bundle tag, then default
    effective_tag = order_id_tag
    if effective_tag is None and order_id_tags:
        effective_tag = order_id_tags[0]  # backward compat: take first from list
    if effective_tag is not None:
        params["order_id_tag"] = effective_tag
    elif "order_id_tag" not in params:
        params["order_id_tag"] = config.tag or "000"

    if "manage_stop" not in params:
        params["manage_stop"] = True

    # Filter to fields the config accepts
    if config_fields:
        filtered = {k: v for k, v in params.items() if k in config_fields}
    else:
        filtered = params

    # Convert string values to NT types
    if "instrument_id" in filtered and isinstance(filtered["instrument_id"], str):
        filtered["instrument_id"] = InstrumentId.from_str(filtered["instrument_id"])
    if "bar_type" in filtered and isinstance(filtered["bar_type"], str):
        filtered["bar_type"] = BarType.from_str(filtered["bar_type"])

    strategy_instance = strategy_cls(config=config_cls(**filtered))
    logger.info(
        "Created strategy instance: %s for %s",
        strategy_cls.__name__, config.symbols,
    )
    return [strategy_instance]


def create_actors(
    config: StrategyBundle,
    *,
    actors_dir: str | Path | None = None,
    strategy_name: str | None = None,
    strategy_tag_prefix: str | None = None,
) -> list[Any]:
    """Create actor instances from a StrategyBundle's actor references.

    When ``config.risk_guard`` is set (declarative format), a RiskGuardActor
    is created automatically.

    Returns a list of instantiated Actor objects.
    """
    name = strategy_name

    if not config.actors and config.risk_guard is None:
        return []

    actors_dir = Path(actors_dir) if actors_dir else _DEFAULT_ACTORS_DIR
    results = []

    # Declarative risk_guard section takes priority
    if config.risk_guard is not None and config.risk_guard.enabled:
        from tinohelm.actors.risk_guard import RiskGuardActor, RiskGuardConfig

        rg_component_id = f"RiskGuard-{name}" if name else "RiskGuardActor"
        rg_config = RiskGuardConfig(
            component_id=rg_component_id,
            strategy_name=name,
            strategy_tag_prefix=strategy_tag_prefix,
            daily_stop_loss_pct=config.risk_guard.daily_stop_loss_pct,
            max_drawdown_pct=config.risk_guard.max_drawdown_pct,
            max_total_exposure=config.risk_guard.max_total_exposure,
            max_positions=config.risk_guard.max_positions,
            breach_action=config.risk_guard.breach_action,
            check_interval_secs=config.risk_guard.check_interval_secs,
            venue_name="BINANCE",
            currency=config.account.currency,
            starting_balance=config.account.starting_balance,
        )
        rg_actor = RiskGuardActor(config=rg_config)
        results.append(rg_actor)
        logger.info("Created RiskGuardActor: %s (strategy=%s)", rg_component_id, name)

        # Skip legacy risk_guard in actors list to avoid duplicate
        legacy_actors = [a for a in (config.actors or []) if a.name != "risk_guard"]
    else:
        legacy_actors = list(config.actors or [])

    for actor_ref in legacy_actors:
        actor_instance = _load_single_actor(
            actor_ref, config, actors_dir,
            strategy_name=name,
            strategy_tag_prefix=strategy_tag_prefix,
        )
        results.append(actor_instance)

    return results


def _load_single_actor(
    ref: ActorRef,
    config: StrategyBundle,
    actors_dir: Path,
    *,
    strategy_name: str | None = None,
    strategy_tag_prefix: str | None = None,
) -> Any:
    """Load and instantiate a single actor from an ActorRef."""
    if ref.name:
        actor_file = actors_dir / f"{ref.name}.py"
        if not actor_file.exists():
            actor_file = _BUILTIN_ACTORS_DIR / f"{ref.name}.py"
        if not actor_file.exists():
            raise FileNotFoundError(
                f"Actor '{ref.name}' not found at {actors_dir / f'{ref.name}.py'} "
                f"or built-in {_BUILTIN_ACTORS_DIR / f'{ref.name}.py'}. "
                f"Create the file or use 'class' to specify a local path."
            )
        actor_cls, actor_config_cls = _discover_actor_classes(actor_file)

    elif ref.class_path:
        if ref.class_path.startswith("./"):
            module_part, class_name = ref.class_path.rsplit(":", 1)
            relative = module_part.removeprefix("./")
            module_file = (config.source_path / (relative + ".py")).resolve()
            if not str(module_file).startswith(str(config.source_path.resolve())):
                raise ValueError(
                    f"Actor class_path '{ref.class_path}' resolves outside strategy folder"
                )
        else:
            module_part, class_name = ref.class_path.rsplit(":", 1)
            module_file = Path(module_part + ".py")
            resolved = module_file.resolve()
            allowed_dirs = [Path.home() / ".tino"]
            if config.source_path:
                allowed_dirs.append(config.source_path.resolve())
            if not any(resolved.is_relative_to(d) for d in allowed_dirs):
                raise ValueError(
                    f"Actor class_path '{ref.class_path}' resolves to {resolved} "
                    f"which is outside allowed directories"
                )

        if not module_file.exists():
            raise FileNotFoundError(f"Actor module not found: {module_file}")

        actor_cls, actor_config_cls = _discover_actor_classes(module_file, class_name)
    else:
        raise ValueError("ActorRef must have either 'name' or 'class' set")

    # Inject isolation for legacy risk_guard actors
    if ref.name == "risk_guard" and strategy_name:
        if ref.params is None:
            ref.params = {}
        ref.params.setdefault("strategy_name", strategy_name)
        ref.params.setdefault("strategy_tag_prefix", strategy_tag_prefix)
        ref.params.setdefault("component_id", f"RiskGuard-{strategy_name}")

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


def _import_strategy_classes(config: StrategyBundle) -> tuple[type, type]:
    """Import strategy class and config class from a StrategyBundle."""
    strategy_module_path, strategy_class_name = config.strategy_class.rsplit(":", 1)
    config_module_path, config_class_name = config.config_class.rsplit(":", 1)

    source_path = config.source_path
    if source_path is None:
        raise ValueError("StrategyBundle.source_path is required for strategy loading")

    strategy_file = _resolve_module_file(strategy_module_path, source_path)

    mod = _load_module_from_file(strategy_file, strategy_module_path)
    strategy_cls = getattr(mod, strategy_class_name)
    config_cls = getattr(mod, config_class_name)

    return strategy_cls, config_cls


def _resolve_module_file(module_path: str, source_path: Path) -> Path:
    """Resolve a module path string to an actual .py file."""
    p = Path(module_path)
    if p.suffix == ".py" and p.exists():
        return p

    module_file = source_path / f"{module_path}.py"
    if module_file.exists():
        return module_file

    if p.is_absolute() and p.exists():
        return p

    for base in [source_path, Path.cwd(), Path("/app")]:
        candidate = base / f"{module_path}.py"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Cannot find module '{module_path}' in {source_path} or other search paths"
    )


def _load_module_from_file(file_path: Path, module_name: str) -> Any:
    """Load a Python module from a file path.

    Delegates to the unified module loader.
    """
    return load_module_from_file(file_path)


def _discover_actor_classes(
    file_path: Path,
    class_name: str | None = None,
) -> tuple[type, type | None]:
    """Discover Actor and ActorConfig subclasses from a .py file."""
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
    """Scan the actors directory for Actor/ActorConfig subclasses."""
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
    """Warn if symbols have no matching SYMBOL_PROFILES entry in the strategy."""
    mod = sys.modules.get(strategy_cls.__module__)
    if mod is None:
        return

    profiles = getattr(mod, "SYMBOL_PROFILES", None)
    if profiles is None:
        return

    for symbol in symbols:
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
                "Symbol '%s' (jesse: '%s') has enabled=False in SYMBOL_PROFILES.",
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


# Backward-compat aliases for private names
_normalize_symbol = normalize_symbol
_make_bar_type_str = make_bar_type_str
_INTERVAL_MAP = INTERVAL_MAP
