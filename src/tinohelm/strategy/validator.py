"""Strategy file validation.

Inspects a user-provided ``.py`` file and reports whether it declares a valid
NautilusTrader :class:`Strategy` + :class:`StrategyConfig` pair, together with
the lifecycle hooks it implements and its declared config fields.

The structural / introspection pieces that do not require NautilusTrader itself
live in :mod:`tinohelm.strategy.validator_helpers` so they can be exercised by
fast unit tests without importing the full NT stack.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tinohelm.strategy.module_loader import discover_strategy_classes, load_module_from_file
from tinohelm.strategy.validator_helpers import (
    STRATEGY_HOOK_NAMES,
    build_missing_hook_warnings,
    collect_implemented_hooks,
    empty_validation_result,
    extract_config_params,
)

logger = logging.getLogger(__name__)


def validate_strategy(name: str, strategies_dir: str | Path) -> dict[str, Any]:
    """Validate a strategy file for structural correctness.

    Returns a result dict with keys ``valid`` / ``name`` / ``errors`` /
    ``warnings`` / ``strategy_class`` / ``config_class`` / ``config_params`` /
    ``hooks``. On any failure path ``valid`` is ``False`` and ``errors`` is
    populated; ``warnings`` is populated for non-fatal issues such as missing
    recommended hooks.
    """
    strategies_dir = Path(strategies_dir)
    file_path = strategies_dir / f"{name}.py"

    result = empty_validation_result(name)

    if not file_path.exists():
        result["errors"].append(f"File not found: {file_path}")
        return result

    try:
        module = load_module_from_file(file_path)
    except Exception as e:
        result["errors"].append(f"Import failed: {e}")
        return result

    strategy_cls, config_cls = discover_strategy_classes(module)
    if strategy_cls is not None:
        result["strategy_class"] = strategy_cls.__name__
    if config_cls is not None:
        result["config_class"] = config_cls.__name__

    if strategy_cls is None:
        result["errors"].append("No Strategy subclass found")
    if config_cls is None:
        result["errors"].append("No StrategyConfig subclass found")

    if strategy_cls is None or config_cls is None:
        return result

    result["config_params"] = extract_config_params(config_cls)
    result["hooks"] = collect_implemented_hooks(strategy_cls, STRATEGY_HOOK_NAMES)
    result["warnings"].extend(build_missing_hook_warnings(result["hooks"]))

    result["valid"] = len(result["errors"]) == 0
    return result
