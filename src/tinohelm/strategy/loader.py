"""Strategy loader — builds ImportableStrategyConfig for NT."""
from __future__ import annotations

from typing import Any


def build_importable_config(
    module_path: str,
    config_module_path: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a dict suitable for creating ImportableStrategyConfig.

    Args:
        module_path: "module_name:StrategyClass"
        config_module_path: "module_name:ConfigClass"
        params: strategy parameter overrides

    Returns:
        Dict with strategy_path, config_path, config keys.
    """
    return {
        "strategy_path": module_path,
        "config_path": config_module_path,
        "config": params or {},
    }
