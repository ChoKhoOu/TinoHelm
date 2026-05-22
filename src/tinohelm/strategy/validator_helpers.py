"""NT-free structural helpers used by :mod:`tinohelm.strategy.validator`.

``validate_strategy`` itself needs to execute a dynamically-imported user
module which may depend on NautilusTrader. Everything *around* that import —
the result-shell factory, hook discovery, warning aggregation, and config
introspection fallback — is pure Python and can be exercised without NT.

Keeping these helpers in a dedicated module lets the ``validator`` facade stay
~60 lines long and makes the behaviour covered by tests without spinning up
the full NT runtime.
"""
from __future__ import annotations

from typing import Any

__all__ = [
    "STRATEGY_HOOK_NAMES",
    "RECOMMENDED_HOOKS",
    "empty_validation_result",
    "collect_implemented_hooks",
    "build_missing_hook_warnings",
    "extract_config_params",
]

#: Hooks that the validator probes for. Order is stable so callers (and
#: ``result["hooks"]`` consumers) see a deterministic listing.
STRATEGY_HOOK_NAMES: tuple[str, ...] = (
    "on_start",
    "on_stop",
    "on_bar",
    "on_quote_tick",
    "on_trade_tick",
    "on_order_filled",
    "on_position_opened",
    "on_position_changed",
    "on_position_closed",
    "on_event",
)

#: Subset whose absence triggers a ``warnings`` entry (but not an error).
RECOMMENDED_HOOKS: tuple[str, ...] = ("on_start", "on_stop")


def empty_validation_result(name: str) -> dict[str, Any]:
    """Return a fresh, fully-keyed result dict seeded with ``valid=False``.

    The dict is callers' "working buffer" — ``validate_strategy`` mutates it
    in place. Giving every caller an identical shape makes the JSON contract
    deterministic regardless of which error path fires.
    """
    return {
        "valid": False,
        "name": name,
        "errors": [],
        "warnings": [],
        "strategy_class": None,
        "config_class": None,
        "config_params": [],
        "hooks": [],
    }


def collect_implemented_hooks(
    cls: type, hook_names: tuple[str, ...] = STRATEGY_HOOK_NAMES
) -> list[str]:
    """Return the hook names that *cls* declares **directly** (not inherited).

    Uses ``cls.__dict__`` (not :func:`hasattr`) so that abstract stub methods
    inherited from :class:`Strategy` do not falsely register as implemented.
    """
    return [h for h in hook_names if h in cls.__dict__]


def build_missing_hook_warnings(implemented_hooks: list[str]) -> list[str]:
    """Return a warning string for every :data:`RECOMMENDED_HOOKS` absent from
    *implemented_hooks*. Order mirrors :data:`RECOMMENDED_HOOKS`.
    """
    return [
        f"{hook} not implemented (recommended)"
        for hook in RECOMMENDED_HOOKS
        if hook not in implemented_hooks
    ]


def extract_config_params(config_cls: type) -> list[dict[str, Any]]:
    """Return introspected config fields, swallowing introspection errors.

    Delegates to :func:`tinohelm.strategy.utils.get_config_fields` which
    handles both Pydantic ``model_fields`` and msgspec ``__struct_fields__``.
    If the underlying call raises (malformed class, exotic metaclass, etc.)
    this returns an empty list — matching legacy behaviour where validation
    should still succeed with ``config_params=[]`` rather than error out.
    """
    from tinohelm.strategy.utils import get_config_fields

    try:
        return get_config_fields(config_cls)
    except Exception:
        return []
