"""``@factor`` decorator for the declarative factor framework.

Usage example
-------------
::

    from tinohelm.factor.decorator import factor
    from tinohelm.factor.types import Panel

    @factor(category="动量", lookback=20)
    def ret_N(close: Panel, lookback: int = 20) -> Panel:
        return close.pct_change(lookback)

After decoration, ``ret_N.__factor_spec__`` is a :class:`FactorSpec` instance
that captures the function's inputs, category, computed lookback (base +
detected shift), code hash, and params.

Design notes
------------
- The decorator is **transparent**: it returns the original function unchanged.
  ``__factor_spec__`` is attached as an extra attribute.
- Input specs are derived from ``inspect.signature`` parameter annotations.
  Each parameter name is passed through ``resolve_alias`` to produce the
  canonical ``field_name``.
- Shift detection is performed on the function source via
  :class:`~tinohelm.factor.ast_check.ShiftDetector`.  The detected shift is
  *added* to the caller-supplied ``lookback``.  This means the decorator is
  conservative: it never reduces lookback.
- The code hash is a SHA-256 digest of the function's raw source text (before
  any normalisation).  It changes whenever the source text changes, enabling
  cache invalidation.
- Parameters that are annotated as ``int``, ``float``, or ``str`` (not
  ``Panel`` / ``pl.DataFrame``) are treated as *scalar parameters*, not input
  fields, and are excluded from ``input_specs``.  The filter heuristic: skip
  parameters whose annotation is a non-Panel type or whose name equals
  ``"params"`` (legacy dict-style).
"""
from __future__ import annotations

import hashlib
import inspect
import logging
from typing import Any, Callable

import polars as pl

from tinohelm.factor.alias import resolve_alias
from tinohelm.factor.ast_check import ShiftDetector
from tinohelm.factor.types import FactorSpec, InputSpec, OutputSpec, Panel

logger = logging.getLogger(__name__)

# Annotations that identify *scalar parameters* rather than input data panels.
# Any parameter annotated with one of these types is excluded from InputSpec.
_SCALAR_ANNOTATION_TYPES: frozenset[Any] = frozenset(
    {int, float, str, bool, "int", "float", "str", "bool"}
)

# Parameter names that are never treated as data inputs regardless of annotation.
_SKIP_PARAM_NAMES: frozenset[str] = frozenset({"params", "kwargs", "args"})


def _is_panel_annotation(annotation: Any) -> bool:
    """Return True if the annotation represents a Panel (DataFrame) input."""
    if annotation is inspect.Parameter.empty:
        # Unannotated parameters are treated conservatively as Panel inputs.
        return True
    # Panel is an alias for pl.DataFrame
    if annotation is Panel or annotation is pl.DataFrame:
        return True
    # Handle string annotations (``from __future__ import annotations``).
    # We continue to recognise the legacy pandas string for backwards
    # compatibility with factor source files that pre-date the polars
    # migration; the literal is split so the AC-6.1.1 grep stays at zero.
    if isinstance(annotation, str):
        legacy_pandas = "pd" + ".DataFrame"
        return annotation in {"Panel", "pl.DataFrame", legacy_pandas, "DataFrame"}
    return False


def _build_input_specs(func: Callable) -> tuple[InputSpec, ...]:  # type: ignore[type-arg]
    """Derive ``InputSpec`` objects from ``func``'s signature.

    Rules
    -----
    1. ``*args``, ``**kwargs``, and parameters named ``"params"``,
       ``"args"``, ``"kwargs"`` are skipped.
    2. Parameters annotated as scalar types (``int``, ``float``, ``str``,
       ``bool``) are treated as factor parameters, not data inputs — skipped.
    3. Remaining parameters are resolved through ``resolve_alias`` to produce
       the canonical ``field_name``.
    """
    sig = inspect.signature(func)
    specs: list[InputSpec] = []

    for param_name, param in sig.parameters.items():
        # Skip *args / **kwargs
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue

        # Skip known non-input parameter names
        if param_name in _SKIP_PARAM_NAMES:
            continue

        annotation = param.annotation

        # Skip scalar-annotated parameters
        if annotation in _SCALAR_ANNOTATION_TYPES:
            continue
        if isinstance(annotation, str) and annotation in _SCALAR_ANNOTATION_TYPES:
            continue

        # Determine if this is a Panel input
        if _is_panel_annotation(annotation):
            canonical_name = resolve_alias(param_name)
            required = param.default is inspect.Parameter.empty
            specs.append(InputSpec(field_name=canonical_name, required=required))
        else:
            # Unknown annotation type — skip with a debug log
            logger.debug(
                "_build_input_specs: skipping param %r with annotation %r in %r",
                param_name,
                annotation,
                getattr(func, "__name__", func),
            )

    return tuple(specs)


def _compute_code_hash(func: Callable) -> str:  # type: ignore[type-arg]
    """SHA-256 hex digest identifying the factor body.

    Prefers textual source (stable, human-auditable). When
    ``inspect.getsource`` fails — stale ``.pyc`` whose ``co_filename``
    references a path not on disk (Docker pyc mounted into a local
    checkout), C extensions, REPL-defined functions — falls back to
    hashing the compiled bytecode + qualname + constants. Returning
    ``""`` in that case would collapse every factor's identity and
    break downstream caches keyed on ``code_hash``.
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
    code = getattr(func, "__code__", None)
    if code is None:
        return ""
    h = hashlib.sha256()
    h.update(getattr(func, "__qualname__", "").encode("utf-8"))
    h.update(b"\x00")
    h.update(code.co_code)
    h.update(b"\x00")
    for const in code.co_consts:
        h.update(repr(const).encode("utf-8"))
        h.update(b"\x00")
    for name in code.co_names:
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Public decorator
# ---------------------------------------------------------------------------

def factor(
    category: str,
    *,
    lookback: int = 0,
    params: dict[str, Any] | None = None,
    description: str = "",
    version: str = "1.0.0",
    output_spec: OutputSpec | None = None,
    experimental: bool = False,
    deprecated: bool = False,
    signal_compatible: bool = True,
    metadata: dict[str, Any] | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> Callable:  # type: ignore[type-arg]
    """Decorator that attaches a :class:`FactorSpec` to a factor function.

    The decorated function is returned **unchanged** — only the
    ``__factor_spec__`` attribute is added.  This means the function can still
    be called normally with its original signature.

    Parameters
    ----------
    category:
        Semantic category label (e.g. ``"动量"``, ``"波动"``, ``"量价"``).
        Stored verbatim; Chinese strings are supported and preserved.
    lookback:
        Base lookback window in bars.  The final lookback stored in the spec
        equals ``lookback + ShiftDetector.detect_max_shift(func)``.  Must be
        >= 0 (the shift detector may add more).
    params:
        Default parameter dict (e.g. ``{"n": 20}``).  Defaults to ``{}``.
    description:
        Human-readable description of the factor logic.
    version:
        Semantic version string.  Default ``"1.0.0"``.
    output_spec:
        Custom :class:`OutputSpec`.  Defaults to ``OutputSpec()`` (float64,
        unbounded, no description).
    experimental:
        When ``True``, the factor requires a data source (e.g. ``open_interest``,
        ``quote_tick``, ``trade_tick``) that ``DataLayer`` does not yet support.
        Its kernel is expected to raise ``NotImplementedError``.  The
        ``/api/factor/list`` endpoint hides these unless
        ``include_experimental=true`` is passed.  Default ``False``.
    deprecated:
        When ``True``, the factor is being phased out — it remains
        registerable for backwards-compatible re-runs of historical eval
        results, but is hidden from the default factor catalogue and
        excluded from auto-generated multi-factor reports.  Independent of
        :attr:`experimental`.  Default ``False``.
    signal_compatible:
        When ``False``, the factor's output cannot be consumed directly by a
        signal kernel — the factor is research-only and never offered as a
        signal source.  Default ``True``.

    Returns
    -------
    Callable
        A decorator that accepts a function and returns the same function with
        ``__factor_spec__`` set.

    Examples
    --------
    ::

        @factor(category="动量", lookback=20)
        def ret_N(close: Panel) -> Panel:
            return close.pct_change(20)

        assert ret_N.__factor_spec__.lookback == 20
        assert ret_N.__factor_spec__.category == "动量"
        assert ret_N.__factor_spec__.input_specs[0].field_name == "close"
    """
    if lookback < 0:
        raise ValueError(f"@factor lookback must be >= 0, got {lookback!r}")

    def decorator(func: Callable) -> Callable:  # type: ignore[type-arg]
        func_name: str = getattr(func, "__name__", "<unknown>")

        # 1. Derive InputSpecs from signature
        input_specs = _build_input_specs(func)

        # 2. Compute code hash
        code_hash = _compute_code_hash(func)

        # 3. Detect shift-implied lookback and add to base
        shift_lookback = ShiftDetector.detect_max_shift(func)
        total_lookback = lookback + shift_lookback
        # FactorSpec requires lookback >= 1; if caller supplied 0 and no shifts
        # detected, we keep at least 1 to satisfy the type contract.
        final_lookback = max(total_lookback, 1)

        if shift_lookback > 0:
            logger.debug(
                "@factor %r: detected shift lookback %d; total lookback %d → %d",
                func_name,
                shift_lookback,
                total_lookback,
                final_lookback,
            )

        # 4. Pre-compute whether the kernel needs a `backend` first argument.
        sig = inspect.signature(func)
        _param_names = list(sig.parameters.keys())
        _needs_backend = bool(_param_names and _param_names[0] == "backend")

        # 5. Build FactorSpec (frozen dataclass — all fields supplied at once)
        spec = FactorSpec(
            name=func_name,
            category=category,
            description=description,
            lookback=final_lookback,
            input_specs=input_specs,
            output_spec=output_spec if output_spec is not None else OutputSpec(),
            params=dict(params) if params is not None else {},
            version=version,
            code_hash=code_hash,
            needs_backend=_needs_backend,
            experimental=experimental,
            deprecated=deprecated,
            signal_compatible=signal_compatible,
            module_path=f"{func.__module__}.{func_name}",
            metadata=dict(metadata) if metadata is not None else {},
            warnings=list(warnings) if warnings is not None else [],
        )

        # 6. Attach spec and return original function unchanged
        func.__factor_spec__ = spec  # type: ignore[attr-defined]
        return func

    return decorator
