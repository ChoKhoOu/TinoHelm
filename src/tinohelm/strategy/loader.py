"""Strategy loader — creates strategy and actor instances from StrategyBundle.

This is the single entry point for strategy/actor instantiation, shared by
BacktestRunner, Sandbox node, and Live node.  Pure logic (symbol parsing,
module-file resolution, parameter building) lives in
:mod:`tinohelm.strategy.loader_helpers` so tests can cover it without NT.
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any

from tinohelm.portfolio.config import StrategyBundle, ActorRef
from tinohelm.strategy.loader_helpers import (
    INTERVAL_MAP,
    UNIT_MAP,
    build_strategy_params,
    check_symbol_profiles,
    make_bar_type_str,
    normalize_symbol,
    nt_symbol_to_jesse,
    parse_interval,
    resolve_actor_class_path,
    resolve_module_file,
)
from tinohelm.strategy.module_loader import load_module_from_file
from tinohelm.strategy.utils import get_config_field_names

logger = logging.getLogger(__name__)

# Default actors directory
_DEFAULT_ACTORS_DIR = Path.home() / ".tino" / "actors"
# Built-in actors shipped with the package
_BUILTIN_ACTORS_DIR = Path(__file__).resolve().parent.parent / "actors"


# ---------------------------------------------------------------------------
# Public helpers re-exported from loader_helpers for import stability.
# External callers (BacktestRunner, scaffold, api.routes.data) import these
# names directly from `tinohelm.strategy.loader`, so we keep them here.
# ---------------------------------------------------------------------------

__all__ = [
    "INTERVAL_MAP",
    "create_actors",
    "create_strategies",
    "make_bar_type_str",
    "normalize_symbol",
    "parse_interval",
    "scan_actors",
]


# ---------------------------------------------------------------------------
# Strategy instantiation
# ---------------------------------------------------------------------------

def create_strategies(
    config: StrategyBundle,
    *,
    order_id_tag: str | None = None,
    order_id_tags: list[str] | None = None,
) -> list[Any]:
    """Create a single strategy instance from a ``StrategyBundle``.

    The strategy receives ``symbols``, ``interval``, and
    ``resolved_bar_types`` in its config.  For backward compatibility, when
    the strategy's config class still declares ``instrument_id`` /
    ``bar_type`` fields, the first symbol's values are injected.

    ``order_id_tags`` (plural) is accepted for backward compatibility with
    the pre-portfolio list-based signature; the first element is used.

    Returns a list containing one instantiated Strategy object.
    """
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.data import BarType

    strategy_cls, config_cls = _import_strategy_classes(config)

    _warn_unrecognized_symbols(strategy_cls, config.symbols)

    config_fields = get_config_field_names(config_cls)

    filtered = build_strategy_params(
        config,
        config_fields or None,
        order_id_tag=order_id_tag,
        order_id_tags=order_id_tags,
    )

    # NT-specific type coercion happens here; everything above is pure.
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


# ---------------------------------------------------------------------------
# Actor instantiation
# ---------------------------------------------------------------------------

def create_actors(
    config: StrategyBundle,
    *,
    actors_dir: str | Path | None = None,
    strategy_name: str | None = None,
    strategy_tag_prefix: str | None = None,
) -> list[Any]:
    """Create actor instances from a ``StrategyBundle``'s actor references.

    When ``config.risk_guard`` is set (declarative format), a
    ``RiskGuardActor`` is created automatically and any legacy
    ``risk_guard`` entry in ``config.actors`` is skipped to avoid a
    duplicate registration.

    Returns a list of instantiated Actor objects.
    """
    name = strategy_name

    if not config.actors and config.risk_guard is None:
        return []

    actors_dir = Path(actors_dir) if actors_dir else _DEFAULT_ACTORS_DIR
    results: list[Any] = []

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
        results.append(RiskGuardActor(config=rg_config))
        logger.info("Created RiskGuardActor: %s (strategy=%s)", rg_component_id, name)

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
        module_file, class_name = resolve_actor_class_path(
            ref.class_path,
            config.source_path,
        )
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

    if actor_config_cls and ref.params:
        config_fields = get_config_field_names(actor_config_cls)
        filtered_params = (
            {k: v for k, v in ref.params.items() if k in config_fields}
            if config_fields
            else ref.params
        )
        actor_config_instance = actor_config_cls(**filtered_params)
        actor_instance = actor_cls(config=actor_config_instance)
    elif actor_config_cls:
        actor_instance = actor_cls(config=actor_config_cls())
    else:
        actor_instance = actor_cls()

    logger.info("Created actor instance: %s", actor_cls.__name__)
    return actor_instance


# ---------------------------------------------------------------------------
# Module & class discovery
# ---------------------------------------------------------------------------

def _import_strategy_classes(config: StrategyBundle) -> tuple[type, type]:
    """Import strategy class and config class from a ``StrategyBundle``."""
    strategy_module_path, strategy_class_name = config.strategy_class.rsplit(":", 1)
    config_module_path, config_class_name = config.config_class.rsplit(":", 1)

    source_path = config.source_path
    if source_path is None:
        raise ValueError("StrategyBundle.source_path is required for strategy loading")

    strategy_file = resolve_module_file(strategy_module_path, source_path)
    mod = _load_module_from_file(strategy_file, strategy_module_path)
    strategy_cls = getattr(mod, strategy_class_name)
    config_cls = getattr(mod, config_class_name)
    return strategy_cls, config_cls


def _load_module_from_file(file_path: Path, module_name: str) -> Any:
    """Load a Python module from a file path via the unified loader.

    ``module_name`` is accepted for backward-compatible API shape but
    ignored — :func:`load_module_from_file` derives a unique name.
    """
    return load_module_from_file(file_path)


def _discover_actor_classes(
    file_path: Path,
    class_name: str | None = None,
) -> tuple[type, type | None]:
    """Discover Actor and ActorConfig subclasses from a ``.py`` file."""
    mod = _load_module_from_file(file_path, file_path.stem)

    actor_cls: type | None = None
    config_cls: type | None = None

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

    results: list[dict[str, Any]] = []
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


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

def _warn_unrecognized_symbols(strategy_cls: type, symbols: list[str]) -> None:
    """Log a warning for each symbol missing/disabled in ``SYMBOL_PROFILES``.

    Thin wrapper over :func:`check_symbol_profiles` — the pure check is in
    ``loader_helpers`` so tests can assert on the raw issue list.
    """
    for symbol, jesse_sym, reason in check_symbol_profiles(strategy_cls, symbols):
        if reason == "missing":
            logger.warning(
                "Symbol '%s' (jesse: '%s') has no entry in SYMBOL_PROFILES. "
                "Will use DEFAULT_PROFILE and may not generate trading signals.",
                symbol, jesse_sym,
            )
        elif reason == "disabled":
            logger.warning(
                "Symbol '%s' (jesse: '%s') has enabled=False in SYMBOL_PROFILES.",
                symbol, jesse_sym,
            )


# ---------------------------------------------------------------------------
# Backward-compat aliases — kept so existing imports (including tests that
# use ``_nt_symbol_to_jesse`` / ``_normalize_symbol`` / ``_make_bar_type_str``
# / ``_INTERVAL_MAP``) continue to work after the helper extraction.
# ---------------------------------------------------------------------------

_nt_symbol_to_jesse = nt_symbol_to_jesse
_normalize_symbol = normalize_symbol
_make_bar_type_str = make_bar_type_str
_INTERVAL_MAP = INTERVAL_MAP
_UNIT_MAP = UNIT_MAP
_resolve_module_file = resolve_module_file
