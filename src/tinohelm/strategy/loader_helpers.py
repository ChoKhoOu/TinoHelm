"""Pure helpers for strategy loading — NT-free for testability.

Every function here is deterministic and has no NautilusTrader dependency.
All symbol/interval parsing, file resolution, class-path validation, and
parameter-dict building that feeds ``strategy.loader`` lives here so that
unit tests can exercise the logic without installing ``nautilus_trader``.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tinohelm.portfolio.config import StrategyBundle


# ---------------------------------------------------------------------------
# Interval / symbol parsing
# ---------------------------------------------------------------------------

INTERVAL_MAP: dict[str, str] = {
    "1m": "1-MINUTE", "3m": "3-MINUTE", "5m": "5-MINUTE",
    "15m": "15-MINUTE", "30m": "30-MINUTE",
    "1h": "1-HOUR", "2h": "2-HOUR", "4h": "4-HOUR",
    "6h": "6-HOUR", "8h": "8-HOUR", "12h": "12-HOUR",
    "1d": "1-DAY",
}

UNIT_MAP: dict[str, str] = {"s": "SECOND", "m": "MINUTE", "h": "HOUR", "d": "DAY"}

_INTERVAL_RE = re.compile(r"^(\d+)([smhd])$")


def parse_interval(interval: str) -> str:
    """Translate a TinoHelm interval string (``"5m"``) to NT format (``"5-MINUTE"``).

    Known values hit the fast-path map; unknown values are parsed
    dynamically from ``<n><unit>`` (unit ∈ ``s/m/h/d``).  Anything that
    fails parsing falls back to ``"1-MINUTE"`` so downstream code still
    receives a well-formed bar-type string.
    """
    if interval in INTERVAL_MAP:
        return INTERVAL_MAP[interval]
    m = _INTERVAL_RE.match(interval.lower())
    if m:
        return f"{m.group(1)}-{UNIT_MAP[m.group(2)]}"
    return "1-MINUTE"


def normalize_symbol(symbol: str) -> str:
    """Return an NT-ready symbol with an explicit ``.BINANCE`` venue suffix.

    Idempotent — an already-suffixed symbol is returned unchanged.
    """
    s = symbol.replace(".BINANCE", "")
    return f"{s}.BINANCE"


def make_bar_type_str(symbol: str, interval: str) -> str:
    """Build an NT bar-type string, e.g. ``BTCUSDT-PERP.BINANCE-5-MINUTE-LAST-EXTERNAL``."""
    nt_symbol = normalize_symbol(symbol)
    interval_part = parse_interval(interval)
    return f"{nt_symbol}-{interval_part}-LAST-EXTERNAL"


def nt_symbol_to_jesse(symbol: str) -> str:
    """Convert an NT/TinoHelm symbol into the Jesse profile-key format.

    Examples:
        ``BTCUSDT-PERP``         → ``BTC-USDT``
        ``ETHUSDT-PERP.BINANCE`` → ``ETH-USDT``
        ``WIFUSDT-SWAP``         → ``WIF-USDT``

    Unrecognised symbols are returned unchanged (after venue stripping),
    so the caller can use the result as a best-effort profile lookup key.
    """
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


# ---------------------------------------------------------------------------
# Module / class-path resolution (pure — no module loading)
# ---------------------------------------------------------------------------

def resolve_module_file(
    module_path: str,
    source_path: Path,
    *,
    extra_search: list[Path] | None = None,
) -> Path:
    """Resolve a ``module_path`` string to a concrete ``.py`` file.

    Search order:
        1. ``module_path`` already points to an existing ``.py`` file.
        2. ``source_path/{module_path}.py``.
        3. ``module_path`` as an absolute path that exists.
        4. ``base/{module_path}.py`` for each ``base`` in
           ``[source_path, cwd, *extra_search]`` (``[source_path, cwd, /app]``
           when ``extra_search`` is not supplied — the production default).

    Raises ``FileNotFoundError`` if no candidate exists.
    """
    p = Path(module_path)
    if p.suffix == ".py" and p.exists():
        return p

    module_file = source_path / f"{module_path}.py"
    if module_file.exists():
        return module_file

    if p.is_absolute() and p.exists():
        return p

    if extra_search is None:
        search_dirs = [source_path, Path.cwd(), Path("/app")]
    else:
        search_dirs = [source_path, Path.cwd(), *extra_search]

    for base in search_dirs:
        candidate = base / f"{module_path}.py"
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        f"Cannot find module '{module_path}' in {source_path} or other search paths"
    )


# NOTE: Not a settings fallback — this is the explicit legacy actor
# class path anchor (e.g. "~/.tino/foo:Bar"). NFR-008 requires keeping
# this reference for backward compatibility with user actor files.
_HOME_TINO_ROOT: Path = Path.home() / ".tino"


def _configured_tino_roots() -> list[Path]:
    """Return candidate root dirs used by ``resolve_actor_class_path``.

    We accept an Actor ``class_path`` that resolves inside any of the
    user-data roots declared in ``settings.paths`` (``strategies``,
    ``actors``, ``research``).  The parent of each configured path is also
    considered so a legacy ``~/.tino/foo:Class`` reference keeps working in
    mixed setups.  Each candidate is kept only when it points at an
    existing directory to avoid false positives on misconfigured env vars.

    Semantics:
      * Read ``strategies``/``actors``/``research`` from PathRegistry; failure
        on any single field is tolerated (per-field ``try/except``) because
        this function is a best-effort aggregator — the caller validates
        individually.
      * Always append ``_HOME_TINO_ROOT`` as a legacy compat anchor so
        ``~/.tino/foo:Bar`` class paths keep resolving in zero-config dev.
      * De-duplicate while preserving order; drop non-existent entries
        only when all-missing (preserve original ``result or [...]``
        semantic for the legacy anchor).
    """
    from tinohelm.core.paths import paths, PathConfigError

    roots: list[Path] = []
    for field in ("strategies", "actors", "research"):
        try:
            p = paths.get(field)
        except PathConfigError:
            continue  # per-field tolerant; not a global silent fallback
        roots.append(p)
        # The parent of each configured root is the canonical ``tino/`` root
        # (e.g. ``/app/tino``).  Including it lets legacy Actor paths such
        # as ``~/.tino/custom_actor:Foo`` resolve through the same check.
        roots.append(p.parent)

    # Always include the legacy home anchor (职责 ii — NFR-008)
    roots.append(_HOME_TINO_ROOT)

    # De-duplicate while preserving order; drop candidates that don't exist
    # so we don't accept paths under an empty env-var misconfiguration.
    seen: set[Path] = set()
    result: list[Path] = []
    for p in roots:
        if p in seen:
            continue
        seen.add(p)
        if p.exists():
            result.append(p)
    # If nothing exists, at least return the legacy anchor so
    # ``resolve_actor_class_path`` has a non-empty candidate list
    return result or [_HOME_TINO_ROOT]


def resolve_actor_class_path(
    class_path: str,
    source_path: Path | None,
    *,
    home_tino_dir: Path | None = None,
) -> tuple[Path, str]:
    """Resolve and validate an Actor ``class_path`` into ``(module_file, class_name)``.

    Supported forms:

    * ``"./module:ClassName"`` — path relative to ``source_path`` (the
      strategy folder).  The resolved file must stay inside ``source_path``.
    * ``"module_path:ClassName"`` — a bare path that must resolve inside
      one of the allowed directories: ``home_tino_dir`` when supplied,
      otherwise the roots returned by :func:`_configured_tino_roots`
      (``settings.paths.strategies`` / ``actors`` / ``research`` plus the
      ``~/.tino`` fallback), or ``source_path``.

    This function performs **only** string parsing and boundary checks.
    Module loading is delegated to ``strategy.module_loader``.

    Raises
    ------
    ValueError
        When the ``class_path`` is malformed or escapes the allowed roots.
    FileNotFoundError
        When the resolved ``.py`` file does not exist.
    """
    if ":" not in class_path:
        raise ValueError(
            f"class_path must be 'module:ClassName', got: {class_path!r}"
        )
    module_part, class_name = class_path.rsplit(":", 1)
    if not class_name:
        raise ValueError(f"class_path missing class name: {class_path!r}")

    if class_path.startswith("./"):
        if source_path is None:
            raise ValueError(
                f"Relative class_path {class_path!r} requires source_path"
            )
        relative = module_part.removeprefix("./")
        module_file = (source_path / (relative + ".py")).resolve()
        if not str(module_file).startswith(str(source_path.resolve())):
            raise ValueError(
                f"Actor class_path '{class_path}' resolves outside strategy folder"
            )
    else:
        raw_file = Path(module_part + ".py")
        resolved = raw_file.resolve()
        if home_tino_dir is not None:
            allowed_dirs: list[Path] = [home_tino_dir]
        else:
            allowed_dirs = list(_configured_tino_roots())
        if source_path is not None:
            allowed_dirs.append(source_path.resolve())
        if not any(resolved.is_relative_to(d) for d in allowed_dirs):
            raise ValueError(
                f"Actor class_path '{class_path}' resolves to {resolved} "
                f"which is outside allowed directories"
            )
        module_file = resolved

    if not module_file.exists():
        raise FileNotFoundError(f"Actor module not found: {module_file}")

    return module_file, class_name


# ---------------------------------------------------------------------------
# Strategy parameter building
# ---------------------------------------------------------------------------

def build_strategy_params(
    bundle: "StrategyBundle",
    config_fields: set[str] | None,
    *,
    order_id_tag: str | None = None,
    order_id_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build the ``kwargs`` dict fed to a strategy config constructor.

    The returned dict holds **string** values for ``instrument_id`` and
    ``bar_type``; NT type conversion (``InstrumentId.from_str`` /
    ``BarType.from_str``) is the caller's responsibility.

    Rules
    -----
    * Always injects ``symbols`` and ``interval``.
    * Injects ``instrument_id`` (first symbol) when the config accepts it.
    * Injects ``bar_type`` (derived from first symbol + interval) when the config accepts it.
    * Order-id tag preference: ``order_id_tag`` > ``order_id_tags[0]`` >
      ``bundle.tag`` > ``"000"``.  Only overwrites an existing
      ``order_id_tag`` when an explicit tag is supplied.
    * Defaults ``manage_stop`` to ``True`` if it isn't already in ``params``.
    * Filters the result to ``config_fields`` (if provided).

    Keeping this function pure means three production consumers
    (BacktestRunner, Sandbox, Live) share exactly the same parameter
    resolution logic and we can unit-test it without NT installed.
    """
    params: dict[str, Any] = dict(bundle.params)
    params["symbols"] = bundle.symbols
    params["interval"] = bundle.interval

    if config_fields and "instrument_id" in config_fields and bundle.symbols:
        params["instrument_id"] = normalize_symbol(bundle.symbols[0])
    if (
        config_fields
        and "bar_type" in config_fields
        and bundle.symbols
        and bundle.interval
    ):
        params["bar_type"] = make_bar_type_str(bundle.symbols[0], bundle.interval)

    effective_tag = order_id_tag
    if effective_tag is None and order_id_tags:
        effective_tag = order_id_tags[0]
    if effective_tag is not None:
        params["order_id_tag"] = effective_tag
    elif "order_id_tag" not in params:
        params["order_id_tag"] = bundle.tag or "000"

    if "manage_stop" not in params:
        params["manage_stop"] = True

    if config_fields:
        return {k: v for k, v in params.items() if k in config_fields}
    return params


# ---------------------------------------------------------------------------
# Symbol-profile validation
# ---------------------------------------------------------------------------

def check_symbol_profiles(
    strategy_cls: type,
    symbols: list[str],
) -> list[tuple[str, str, str]]:
    """Validate symbols against a strategy module's ``SYMBOL_PROFILES``.

    Returns a list of ``(symbol, jesse_symbol, reason)`` tuples where
    ``reason`` is one of:

    * ``"missing"``  — the jesse key isn't in ``SYMBOL_PROFILES``.
    * ``"disabled"`` — the entry has ``enabled=False``.

    Returns an empty list when the strategy module doesn't declare
    ``SYMBOL_PROFILES`` at all, or when the module can't be resolved via
    ``sys.modules`` (a best-effort check).
    """
    mod = sys.modules.get(strategy_cls.__module__)
    if mod is None:
        return []

    profiles = getattr(mod, "SYMBOL_PROFILES", None)
    if profiles is None:
        return []

    issues: list[tuple[str, str, str]] = []
    for symbol in symbols:
        jesse_sym = nt_symbol_to_jesse(symbol)
        profile = profiles.get(jesse_sym)
        if profile is None:
            issues.append((symbol, jesse_sym, "missing"))
        elif not profile.get("enabled", True):
            issues.append((symbol, jesse_sym, "disabled"))
    return issues
