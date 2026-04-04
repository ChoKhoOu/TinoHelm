"""Strategy file validation."""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any

from tinohelm.strategy.module_loader import load_module_from_file

logger = logging.getLogger(__name__)


def validate_strategy(name: str, strategies_dir: str | Path) -> dict[str, Any]:
    """Validate a strategy file for structural correctness.

    Returns validation result dict.
    """
    strategies_dir = Path(strategies_dir)

    file_path = strategies_dir / f"{name}.py"

    result: dict[str, Any] = {
        "valid": False,
        "name": name,
        "errors": [],
        "warnings": [],
        "strategy_class": None,
        "config_class": None,
        "config_params": [],
        "hooks": [],
    }

    if not file_path.exists():
        result["errors"].append(f"File not found: {file_path}")
        return result

    # Try import
    try:
        module = load_module_from_file(file_path)
    except Exception as e:
        result["errors"].append(f"Import failed: {e}")
        return result

    # Find classes
    strategy_cls = None
    config_cls = None

    for obj_name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module.__name__:
            continue
        for base in inspect.getmro(obj):
            if base.__name__ == "Strategy" and base.__module__.startswith("nautilus_trader"):
                strategy_cls = obj
                result["strategy_class"] = obj_name
            if base.__name__ == "StrategyConfig" and base.__module__.startswith("nautilus_trader"):
                config_cls = obj
                result["config_class"] = obj_name

    if not strategy_cls:
        result["errors"].append("No Strategy subclass found")
    if not config_cls:
        result["errors"].append("No StrategyConfig subclass found")

    if not strategy_cls or not config_cls:
        return result

    # Check config params (supports both Pydantic model_fields and msgspec __struct_fields__)
    from tinohelm.strategy.utils import get_config_fields
    if config_cls:
        try:
            result["config_params"] = get_config_fields(config_cls)
        except Exception:
            pass

    # Check hooks
    for hook in ["on_start", "on_stop", "on_bar", "on_quote_tick", "on_trade_tick",
                  "on_order_filled", "on_position_opened", "on_position_changed",
                  "on_position_closed", "on_event"]:
        if hook in strategy_cls.__dict__:
            result["hooks"].append(hook)

    if "on_start" not in result["hooks"]:
        result["warnings"].append("on_start not implemented (recommended)")
    if "on_stop" not in result["hooks"]:
        result["warnings"].append("on_stop not implemented (recommended)")

    result["valid"] = len(result["errors"]) == 0
    return result
