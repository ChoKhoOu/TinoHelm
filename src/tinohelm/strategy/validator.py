"""Strategy file validation."""
from __future__ import annotations

import importlib
import inspect
import logging
import sys
from pathlib import Path
from typing import Any

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

    # Add to path
    str_dir = str(strategies_dir.resolve())
    if str_dir not in sys.path:
        sys.path.insert(0, str_dir)

    # Try import
    try:
        if name in sys.modules:
            del sys.modules[name]
        module = importlib.import_module(name)
    except Exception as e:
        result["errors"].append(f"Import failed: {e}")
        return result

    # Find classes
    strategy_cls = None
    config_cls = None

    for obj_name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != name:
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

    # Check config has instrument_id
    if hasattr(config_cls, "model_fields"):
        fields = config_cls.model_fields
        if "instrument_id" not in fields:
            result["warnings"].append("Config missing 'instrument_id' field (recommended)")

        for field_name, field_info in fields.items():
            result["config_params"].append({
                "name": field_name,
                "type": str(field_info.annotation) if field_info.annotation else "Any",
                "required": field_info.is_required(),
            })

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
