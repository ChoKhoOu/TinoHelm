"""ExposureProvider registry — string-to-instance resolution layer.

EvalConfig.neutralize stores provider names as strings (JSON-serialisable,
frozen-dataclass safe). Aligner resolves those strings to ExposureProvider
instances via this module.

Public API
----------
register(name, cls) -> None
    Register a custom or test ExposureProvider class. Raises ValueError if
    the same name is already registered with a different class.

resolve(name, **kwargs) -> ExposureProvider
    Instantiate a registered provider by name. Raises KeyError for unknowns.

list_providers() -> list[str]
    Return sorted list of all known provider names (builtin + user).
"""

from __future__ import annotations

from typing import Any

from tinohelm.aligner.exposure import BTCBetaExposure, ExposureProvider, LogMcapExposure

# ---------------------------------------------------------------------------
# Internal tables
# ---------------------------------------------------------------------------

# Builtin registry skeleton — s07 will populate with concrete classes.
# Kept as dict[str, type] (not dict[str, type[ExposureProvider]]) so that
# test fake classes (which don't inherit from Protocol) can also be stored.
_BUILTIN_PROVIDERS: dict[str, type] = {
    # Populated here so that the dict is *not* empty in s07's work; the
    # Protocol skeleton classes satisfy the structural subtyping check.
    "btc_beta": BTCBetaExposure,
    "log_mcap": LogMcapExposure,
}

_USER_PROVIDERS: dict[str, type] = {}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def register(name: str, cls: type) -> None:
    """Register a custom ExposureProvider implementation class.

    Third-party and test usage; call before ``resolve``.

    Parameters
    ----------
    name:
        String key used in ``EvalConfig.neutralize``.
    cls:
        Class that structurally satisfies ``ExposureProvider`` (name attr +
        get_exposure method).

    Raises
    ------
    ValueError
        If ``name`` is already registered with a *different* class (prevents
        silent override of builtins or duplicate registrations).
    """
    if name in _BUILTIN_PROVIDERS and _BUILTIN_PROVIDERS[name] is not cls:
        raise ValueError(
            f"provider '{name}' already registered"
            f" (builtin). Cannot override with a different class."
        )
    if name in _USER_PROVIDERS and _USER_PROVIDERS[name] is not cls:
        raise ValueError(f"provider '{name}' already registered")
    _USER_PROVIDERS[name] = cls


def resolve(name: str, **kwargs: Any) -> ExposureProvider:
    """Resolve a provider name to a freshly instantiated ExposureProvider.

    Priority: user-registered > builtin (allows test injection / override).

    Parameters
    ----------
    name:
        Registered provider name.
    **kwargs:
        Forwarded to the class constructor.

    Returns
    -------
    ExposureProvider
        A new instance of the resolved class.

    Raises
    ------
    KeyError
        If ``name`` is not found in either registry.
    """
    cls = _USER_PROVIDERS.get(name) or _BUILTIN_PROVIDERS.get(name)
    if cls is None:
        raise KeyError(
            f"Unknown ExposureProvider: '{name}'. "
            f"Builtin: {list(_BUILTIN_PROVIDERS.keys())}, "
            f"User: {list(_USER_PROVIDERS.keys())}"
        )
    return cls(**kwargs)


def list_providers() -> list[str]:
    """Return sorted list of all registered provider names.

    Includes both builtins and user-registered providers.
    """
    return sorted({**_BUILTIN_PROVIDERS, **_USER_PROVIDERS}.keys())
