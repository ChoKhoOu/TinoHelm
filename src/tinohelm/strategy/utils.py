"""Shared utilities for strategy config introspection and lifecycle support.

Supports both Pydantic (model_fields) and msgspec (__struct_fields__) config classes.
Also provides L1 soft-pause lifecycle helpers for strategies.
"""
from __future__ import annotations

import typing
import weakref
from typing import Any

from tinohelm.node.topics import LIFECYCLE_PAUSE, LIFECYCLE_RESUME

# Sentinel for "no default"
_MISSING = object()


def get_config_fields(cls: type) -> list[dict[str, Any]]:
    """Extract config field metadata from a strategy config class.

    Works with both Pydantic models (``model_fields``) and msgspec Structs
    (``__struct_fields__`` / ``__struct_defaults__``).

    Returns a list of dicts, each with keys:
        - name: field name (str)
        - type: type annotation as string
        - required: whether the field has no default (bool)
        - default: default value, or None if required
    """
    if hasattr(cls, "model_fields"):
        return _extract_pydantic_fields(cls)
    if hasattr(cls, "__struct_fields__"):
        return _extract_msgspec_fields(cls)
    return []


def get_config_field_names(cls: type) -> set[str]:
    """Return just the set of field names accepted by a config class."""
    if hasattr(cls, "model_fields"):
        return set(cls.model_fields.keys())
    if hasattr(cls, "__struct_fields__"):
        return set(cls.__struct_fields__)
    return set()


# ---------------------------------------------------------------------------
# L1 Soft-Pause lifecycle helpers
# ---------------------------------------------------------------------------

# External pause state — Cython Strategy doesn't allow dynamic attribute setting
_pause_state: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def setup_pause_support(strategy: Any) -> None:
    """Wire L1 soft-pause lifecycle support onto a strategy instance.

    Call this in ``on_start()``. Uses a module-level WeakKeyDictionary
    because NT Strategy is a Cython class that forbids dynamic attributes.

    Usage in strategy::

        def on_start(self):
            setup_pause_support(self)
            self.subscribe_bars(self.bar_type)
            ...

        def on_bar(self, bar):
            if is_paused(self):
                return
            ...
    """
    _pause_state[strategy] = False

    def _on_pause(msg: Any) -> None:
        _pause_state[strategy] = True
        strategy.log.warning("Strategy PAUSED by lifecycle controller")

    def _on_resume(msg: Any) -> None:
        _pause_state[strategy] = False
        strategy.log.info("Strategy RESUMED by lifecycle controller")

    strategy.msgbus.subscribe(f"{LIFECYCLE_PAUSE}.{strategy.id}", _on_pause)
    strategy.msgbus.subscribe(f"{LIFECYCLE_RESUME}.{strategy.id}", _on_resume)


def is_paused(strategy: Any) -> bool:
    """Check if a strategy is currently paused via L1 lifecycle control."""
    return _pause_state.get(strategy, False)


# ---------------------------------------------------------------------------
# Optimize range parsing
# ---------------------------------------------------------------------------

_VALID_OPT_TYPES = {"int", "float"}


def parse_optimize_ranges(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate and normalize an OPTIMIZE / optimize YAML dict.

    Returns only entries that have both ``min`` and ``max`` keys.
    Invalid entries are silently skipped.
    """
    ranges: dict[str, dict[str, Any]] = {}
    for pname, pspec in raw.items():
        if not isinstance(pspec, dict) or "min" not in pspec or "max" not in pspec:
            continue
        ptype = pspec.get("type", "float")
        if ptype not in _VALID_OPT_TYPES:
            ptype = "float"
        entry: dict[str, Any] = {"type": ptype, "min": pspec["min"], "max": pspec["max"]}
        if "step" in pspec:
            entry["step"] = pspec["step"]
        ranges[pname] = entry
    return ranges


def _extract_pydantic_fields(cls: type) -> list[dict[str, Any]]:
    fields = []
    for field_name, field_info in cls.model_fields.items():
        fields.append({
            "name": field_name,
            "type": str(field_info.annotation) if field_info.annotation else "Any",
            "required": field_info.is_required(),
            "default": str(field_info.default) if field_info.default is not None else None,
        })
    return fields


def _extract_msgspec_fields(cls: type) -> list[dict[str, Any]]:
    hints = typing.get_type_hints(cls) if hasattr(cls, "__annotations__") else {}
    all_fields = cls.__struct_fields__  # tuple of field names
    defaults_tuple = getattr(cls, "__struct_defaults__", ())
    # __struct_defaults__ is a tuple of defaults for the *last N* fields
    num_defaults = len(defaults_tuple)
    num_required = len(all_fields) - num_defaults
    fields = []
    for i, field_name in enumerate(all_fields):
        if i < num_required:
            has_default = False
            default_val = _MISSING
        else:
            has_default = True
            default_val = defaults_tuple[i - num_required]
        fields.append({
            "name": field_name,
            "type": str(hints.get(field_name, "Any")),
            "required": not has_default,
            "default": str(default_val) if default_val is not _MISSING else None,
        })
    return fields
