"""Factor registry — discovers built-in + custom factors."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from tinohelm.research.factors import BUILTIN_FACTORS, _COMPUTE_MAP

logger = logging.getLogger(__name__)


def _custom_factors_dir() -> Path:
    from tinohelm.core.config import get_settings
    return get_settings().paths.research / "factors"


def _load_custom_factor(path: Path) -> dict | None:
    """Load a custom factor .py file."""
    try:
        spec = importlib.util.spec_from_file_location(f"custom_factor_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        meta = getattr(mod, "FACTOR_META", None)
        compute_fn = getattr(mod, "compute", None)
        if not meta or not compute_fn:
            logger.warning("Custom factor %s missing FACTOR_META or compute()", path)
            return None

        name = meta.get("name", path.stem)
        return {"name": name, "meta": meta, "compute": compute_fn}
    except Exception as exc:
        logger.warning("Failed to load custom factor %s: %s", path, exc)
        return None


def get_all_factors() -> dict[str, dict[str, Any]]:
    """Return all factors (built-in + custom) with metadata."""
    result = {}

    # Built-in
    for name, meta in BUILTIN_FACTORS.items():
        result[name] = {**meta, "source": "builtin"}

    # Custom
    cdir = _custom_factors_dir()
    if cdir.exists():
        for py_file in cdir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            loaded = _load_custom_factor(py_file)
            if loaded:
                result[loaded["name"]] = {**loaded["meta"], "source": "custom"}

    return result


def get_compute_fn(factor_name: str):
    """Get compute function for a factor (built-in or custom)."""
    # Built-in first
    if factor_name in _COMPUTE_MAP:
        return _COMPUTE_MAP[factor_name]

    # Custom
    cdir = _custom_factors_dir()
    if cdir.exists():
        for py_file in cdir.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            loaded = _load_custom_factor(py_file)
            if loaded and loaded["name"] == factor_name:
                return loaded["compute"]

    raise ValueError(f"Unknown factor: {factor_name}")
