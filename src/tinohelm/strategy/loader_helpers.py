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
      one of the allowed directories: ``home_tino_dir`` (defaults to
      ``~/.tino``) or ``source_path``.

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
        tino_root = home_tino_dir or (Path.home() / ".tino")
        allowed_dirs: list[Path] = [tino_root]
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
    * Injects ``resolved_bar_types`` when non-empty.
    * Injects ``instrument_id`` (first symbol) when the config accepts it.
    * Injects ``bar_type`` (first resolved bar type, or derived from first
      symbol + interval) when the config accepts it.
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
    if bundle.resolved_bar_types:
        params["resolved_bar_types"] = bundle.resolved_bar_types

    if config_fields and "instrument_id" in config_fields and bundle.symbols:
        params["instrument_id"] = normalize_symbol(bundle.symbols[0])
    if (
        config_fields
        and "bar_type" in config_fields
        and bundle.symbols
        and bundle.interval
    ):
        if bundle.resolved_bar_types:
            params["bar_type"] = bundle.resolved_bar_types[0]
        else:
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
