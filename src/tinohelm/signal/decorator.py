"""``@signal`` decorator for the declarative signal framework.

Mirrors :func:`tinohelm.factor.decorator.factor` exactly — it attaches a
frozen :class:`~tinohelm.signal.types.SignalSpec` to the kernel function
via the ``__signal_spec__`` attribute and returns the original function
unchanged.

Usage example
-------------
::

    from tinohelm.signal import signal

    @signal(
        name="momentum_top3_long_short",
        factor_ref="ret_N@1.0.0",
        method="top_k_long_short",
        weighting="equal",
        rebalance_freq="1D",
        universe_ref="top10_perp",
        factor_params={"lookback": 20},
        method_params={"k": 3},
    )
    def my_kernel(factor_panel):
        return ...

    assert my_kernel.__signal_spec__.code_hash != ""

Design notes
------------
- Identical hashing approach to ``@factor``: SHA-256 over
  ``inspect.getsource(func)``.  This guarantees ``code_hash`` flips
  whenever the kernel source text changes (any whitespace / comment /
  body edit).  Falls back to the empty string when source cannot be
  retrieved (built-in / C extension / REPL-defined).
- Position-constraint validation is handled implicitly by Python type
  hints + the dataclass; we do an explicit numeric guard here for the
  three required upper bounds because zero or negative values would
  silently degrade :func:`normalize_to_constraints` (allow-everything or
  no-positions edge cases).
- The decorator does NOT wrap the function — the original kernel object
  is returned.  This mirrors ``@factor`` and avoids the closure-side
  effects callers found surprising during the factor framework rebuild.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import marshal
from typing import Any, Callable

from tinohelm.signal.types import (
    CostModel,
    SignalMethod,
    SignalSpec,
    SignalWeighting,
)

logger = logging.getLogger(__name__)


def _compute_code_hash(func: Callable) -> str:  # type: ignore[type-arg]
    """Return the SHA-256 hex digest identifying the function's body.

    Prefers the textual source so hashes stay human-auditable. When
    ``inspect.getsource`` fails — typically because the bytecode cache
    (``.pyc``) carries a ``co_filename`` that no longer exists on disk
    (Docker-built pyc mounted into a local checkout) or for C-extension
    functions — we fall back to hashing the function's module + qualname
    + name together with the marshaled compiled bytecode (see
    :func:`_bytecode_fallback_hash`). That still buys:

    - non-empty, deterministic, per-function identity (two distinct
      bodies compile to distinct bytecode sequences, and two kernels
      sharing bytecode are still separated by their qualnames), and
    - stability across re-imports of the same source (same bytecode).

    Returning ``""`` in this situation would collapse every signal's
    identity into the same empty string and break downstream caches
    keyed on ``code_hash``.
    """
    try:
        source = inspect.getsource(func)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()
    except (OSError, TypeError):
        logger.warning(
            "_compute_code_hash: source unavailable for %r — using bytecode fallback",
            getattr(func, "__qualname__", getattr(func, "__name__", func)),
        )
        return _bytecode_fallback_hash(func)


def _bytecode_fallback_hash(func: Callable) -> str:  # type: ignore[type-arg]
    """Hash identity = ``module`` + ``qualname`` + ``name`` + marshaled bytecode.

    Mixing the function's identifying names in *before* the marshaled code
    object keeps two kernels with identical bytecode but different
    qualnames from collapsing to the same hash. That matters when a code
    object is shared across names (``types.FunctionType(shared_code, …)``,
    ``func.__code__ = other.__code__``) or when trivial wrappers compile
    to byte-identical bodies across modules.
    """
    h = hashlib.sha256()
    h.update(getattr(func, "__module__", "").encode("utf-8"))
    h.update(b"\x00")
    h.update(getattr(func, "__qualname__", "").encode("utf-8"))
    h.update(b"\x00")
    h.update(getattr(func, "__name__", "").encode("utf-8"))
    h.update(b"\x00")
    code = getattr(func, "__code__", None)
    if code is not None:
        h.update(marshal.dumps(code))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public decorator
# ---------------------------------------------------------------------------

def signal(
    *,
    name: str,
    factor_ref: str,
    method: SignalMethod,
    rebalance_freq: str,
    universe_ref: str,
    weighting: SignalWeighting = "equal",
    factor_params: dict[str, Any] | None = None,
    method_params: dict[str, Any] | None = None,
    cost_model: CostModel | None = None,
    gross_exposure: float = 1.0,
    net_exposure: float = 0.0,
    max_position: float = 0.10,
    turnover_budget: float | None = None,
    extra_warmup_bars: int = 0,
    version: str = "1.0.0",
    description: str = "",
    deprecated: bool = False,
) -> Callable:  # type: ignore[type-arg]
    """Decorator that attaches a :class:`SignalSpec` to a kernel function.

    The decorated function is returned **unchanged**; only the
    ``__signal_spec__`` attribute is added.

    Parameters
    ----------
    name:
        Unique signal identifier.
    factor_ref:
        ``"<name>@<version>"`` reference to the upstream factor.
    method:
        Built-in kernel slug (Literal of 5 values).
    rebalance_freq:
        Rebalance cadence (e.g. ``"1D"``).
    universe_ref:
        Universe name resolved at runtime.
    weighting:
        Weight regime — defaults to ``"equal"``.
    factor_params:
        Upstream factor-kernel parameter overrides (e.g. ``{"lookback": 20}``).
        These are kept separate from signal-kernel ``method_params`` so
        research runs and NT exports can reproduce the same factor panel.
    method_params:
        Method-specific kwargs (e.g. ``{"k": 10}``).
    cost_model:
        Cost specification.  Defaults to a ``taker_8bps`` preset.
    gross_exposure / net_exposure / max_position:
        Position constraints (must be > 0; ``net_exposure`` may be 0 for
        a market-neutral signal).
    turnover_budget:
        Optional daily turnover upper bound.
    extra_warmup_bars:
        Extra warmup buffer added to the factor's lookback.
    version / description / deprecated:
        Metadata.

    Raises
    ------
    ValueError
        - ``gross_exposure <= 0`` or ``max_position <= 0``.
        - ``net_exposure < 0``.
        - ``extra_warmup_bars < 0``.
        - ``turnover_budget`` is non-None and ``<= 0``.

    Returns
    -------
    Callable
        A decorator that attaches ``__signal_spec__`` and returns the
        original function.

    Examples
    --------
    ::

        @signal(name="x", factor_ref="ret_N@1.0.0",
                method="top_k_long_short", rebalance_freq="1D",
                universe_ref="top10")
        def kernel(factor_panel):
            return factor_panel
        assert kernel.__signal_spec__.code_hash != ""
    """
    # Numeric guards — must mirror SignalEvaluator's expectations.
    if gross_exposure <= 0:
        raise ValueError(
            f"@signal gross_exposure must be > 0, got {gross_exposure!r}"
        )
    if max_position <= 0:
        raise ValueError(
            f"@signal max_position must be > 0, got {max_position!r}"
        )
    if net_exposure < 0:
        raise ValueError(
            f"@signal net_exposure must be >= 0, got {net_exposure!r}"
        )
    if extra_warmup_bars < 0:
        raise ValueError(
            f"@signal extra_warmup_bars must be >= 0, got {extra_warmup_bars!r}"
        )
    if turnover_budget is not None and turnover_budget <= 0:
        raise ValueError(
            f"@signal turnover_budget must be > 0 when set, got {turnover_budget!r}"
        )

    def decorator(func: Callable) -> Callable:  # type: ignore[type-arg]
        code_hash = _compute_code_hash(func)

        spec = SignalSpec(
            name=name,
            factor_ref=factor_ref,
            method=method,
            weighting=weighting,
            rebalance_freq=rebalance_freq,
            universe_ref=universe_ref,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            max_position=max_position,
            turnover_budget=turnover_budget,
            factor_params=dict(factor_params) if factor_params is not None else {},
            method_params=dict(method_params) if method_params is not None else {},
            cost_model=cost_model
            if cost_model is not None
            else CostModel(name="taker_8bps"),
            extra_warmup_bars=extra_warmup_bars,
            version=version,
            code_hash=code_hash,
            description=description,
            deprecated=deprecated,
        )

        func.__signal_spec__ = spec  # type: ignore[attr-defined]
        return func

    return decorator
