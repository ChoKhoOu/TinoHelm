"""Core data types for the declarative signal framework.

Design notes
------------
- :class:`SignalSpec` mirrors :class:`tinohelm.factor.types.FactorSpec`'s
  declarative pattern (frozen dataclass + ``code_hash`` + ``params``-style
  dict with ``compare=False, hash=False``).  It is the single source of
  truth that drives signal registration, kernel dispatch, evaluation, and
  the warmup-bars formula consumed by :mod:`tinohelm.nt_adapter`.
- :class:`CostModel` is the unified cost description shared between research
  (``SignalEvaluator``) and live (``MetricsActor`` cost-deviation monitor).
  Storing it on :class:`SignalSpec` (instead of a free-form dict) makes the
  research-to-live PIT contract auditable.
- ``SignalSpec.method`` is a :class:`typing.Literal` of the 5 built-in
  kernel slugs.  Future custom kernels live as user ``.py`` files under
  ``paths.get("signals_dir")`` and use ``method="custom"`` would require
  Literal extension; for now the 5 slugs cover all built-in kernels.
- ``SignalSpec`` does NOT carry the underlying factor's ``lookback`` — it
  is derived at runtime from ``FactorSpec(name=signal_spec.factor_ref)``.
  ``extra_warmup_bars`` lets the signal layer add its own buffer (e.g.
  rebalance smoothing) on top of the factor lookback.
- All position-constraint fields (``gross_exposure`` / ``net_exposure`` /
  ``max_position`` / ``turnover_budget``) are top-level scalars, NOT a
  nested dict.  This keeps the spec serialisable, hashable, and trivially
  comparable when persisted to ``signal_runs.config`` JSONB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------------
# CostModel — research/live cost description
# ---------------------------------------------------------------------------

#: Human-readable cost preset names. ``"custom"`` is for user-supplied bps
#: triples that don't match a preset.
CostModelName = Literal["taker_8bps", "maker_2bps_with_rebate", "custom"]


@dataclass(frozen=True)
class CostModel:
    """Cost model declaration — must be consistent between research and live.

    Attributes
    ----------
    name:
        One of three preset slugs:
        ``"taker_8bps"`` — typical taker fee (e.g. Binance Futures USDT).
        ``"maker_2bps_with_rebate"`` — passive maker fee with rebate.
        ``"custom"`` — user-supplied bps triple.
    fee_bps_per_side:
        Exchange fee per side in basis points (1 bps = 0.01%).
    slippage_bps_per_side:
        Expected slippage per side in basis points.  Aggregates impact +
        latency.  Field name retained from the legacy evaluator for
        consistency.
    rebate_bps_per_side:
        Maker rebate per side in basis points.  Subtracted from the
        effective cost so a maker-with-rebate model can be net negative.

    Notes
    -----
    Total per-side cost (in bps) = ``fee_bps_per_side + slippage_bps_per_side
    - rebate_bps_per_side``.  Round-trip cost = 2 × per-side.  The
    :class:`tinohelm.signal.evaluator.SignalEvaluator` is responsible for
    converting bps to fractional drag against turnover.
    """

    name: CostModelName
    fee_bps_per_side: float = 4.0
    slippage_bps_per_side: float = 1.0
    rebate_bps_per_side: float = 0.0


# ---------------------------------------------------------------------------
# SignalSpec — frozen declarative spec with ``code_hash`` for cache invalidation
# ---------------------------------------------------------------------------

#: Slug of one of the 5 built-in signal kernels.
SignalMethod = Literal[
    "top_k_long_short",
    "quantile_long_short",
    "threshold_signed",
    "zscore_clip",
    "rank_to_weight",
]

#: How weights are scaled within the chosen method.  ``"equal"`` uses the
#: kernel's own scaling; the other three reserve hooks for IC/IR/risk-parity
#: weighted variants implemented in :mod:`tinohelm.signal.evaluator` (s15).
SignalWeighting = Literal[
    "equal",
    "ic_weighted",
    "ir_weighted",
    "risk_parity",
]


@dataclass(frozen=True)
class SignalSpec:
    """Declarative specification for a portfolio signal.

    The spec is the single source of truth that drives:

    * @signal decorator → attaches as ``__signal_spec__`` on the kernel.
    * SignalRegistry → keys lookups by ``name``.
    * SignalEvaluator → consumes ``method``, ``method_params``,
      ``cost_model``, exposure constraints.
    * NT adapter → derives ``warmup_bars = FactorSpec(name=factor_ref).lookback
      + extra_warmup_bars``.
    * signal_runs.config JSONB persistence → all scalar fields + dict.

    Attributes
    ----------
    name:
        Unique signal identifier (e.g. ``"momentum_top10_long_short"``).
    factor_ref:
        Reference to the upstream factor in ``"<name>@<version>"`` form
        (e.g. ``"ret_N@1.0.0"``).  Used to look up the FactorSpec that
        provides ``lookback`` and computes the input panel.
    method:
        One of 5 built-in kernel slugs.  Selects which function in
        :mod:`tinohelm.signal.kernels` is invoked.
    weighting:
        Weight-scaling regime (``"equal"`` / ``"ic_weighted"`` /
        ``"ir_weighted"`` / ``"risk_parity"``).  Default ``"equal"`` —
        the kernel's natural scaling applies untouched.
    rebalance_freq:
        Rebalance cadence string (e.g. ``"1D"``, ``"4H"``, ``"1W"``).
        Drives signal evaluator's grouping and turnover annualisation.
    universe_ref:
        Universe name resolved at runtime to ``universes.id``.

    Position constraints (units: portfolio fraction)
    -------------------------------------------------
    gross_exposure:
        Upper bound on Σ|wᵢ| per timestamp.  Default 1.0.
    net_exposure:
        Upper bound on |Σwᵢ| per timestamp.  Default 0.0 (market neutral).
    max_position:
        Upper bound on |wᵢ| per asset per timestamp.  Default 0.10.
    turnover_budget:
        Upper bound on daily turnover (Σ|Δw| / 2).  ``None`` = unconstrained.

    Method parameters
    -----------------
    method_params:
        Method-specific kwargs.  Examples::

            top_k_long_short:    {"k": 10}
            quantile_long_short: {"quantiles": 5, "long_q": 4, "short_q": 0}
            threshold_signed:    {"upper": 0.5, "lower": -0.5,
                                  "long_weight": 0.5, "short_weight": -0.5}
            zscore_clip:         {"clip": 3.0}
            rank_to_weight:      {"power": 1.0}

        Marked ``compare=False, hash=False`` so :class:`SignalSpec`
        instances stay hashable even when the dict carries non-hashable
        nested structures (mirrors :class:`FactorSpec.params`).

    Cost model
    ----------
    cost_model:
        :class:`CostModel` instance.  Defaults to a taker-8bps preset.

    Warmup
    ------
    extra_warmup_bars:
        Additional warmup beyond the factor's lookback.  Real warmup =
        ``FactorSpec(factor_ref).lookback + extra_warmup_bars``.  Used by
        :mod:`tinohelm.nt_adapter` to compute the strategy's
        ``warmup_bars`` config.  Default 0.

    Other metadata
    --------------
    version:
        Semantic version string.  Bump on logic changes.  Default
        ``"1.0.0"``.
    code_hash:
        SHA-256 hex digest of the kernel function source.  Populated by
        the :func:`~tinohelm.signal.decorator.signal` decorator; defaults
        to empty string before decoration.
    description:
        Human-readable description.
    deprecated:
        When ``True``, the signal is hidden from default catalogues but
        still registerable for backwards-compatible re-runs.

    Examples
    --------
    >>> @signal(name="my_signal", factor_ref="ret_N@1.0.0",
    ...         method="top_k_long_short", weighting="equal",
    ...         rebalance_freq="1D", universe_ref="top10_perp",
    ...         method_params={"k": 3})
    ... def my_kernel(factor_panel):
    ...     ...
    >>> spec = my_kernel.__signal_spec__
    >>> spec.name
    'my_signal'
    """

    name: str
    factor_ref: str
    method: SignalMethod
    weighting: SignalWeighting
    rebalance_freq: str
    universe_ref: str

    # Position constraints (top-level scalars — keeps SignalSpec hashable).
    gross_exposure: float = 1.0
    net_exposure: float = 0.0
    max_position: float = 0.10
    turnover_budget: float | None = None

    # method-specific parameters — excluded from hash/compare for
    # consistency with FactorSpec.params handling.
    method_params: dict[str, Any] = field(
        default_factory=dict, compare=False, hash=False
    )

    # cost model
    cost_model: CostModel = field(
        default_factory=lambda: CostModel(name="taker_8bps")
    )

    # warmup (PIT consistency) — actual warmup_bars =
    # FactorSpec(factor_ref).lookback + extra_warmup_bars
    extra_warmup_bars: int = 0

    # other metadata
    version: str = "1.0.0"
    code_hash: str = ""
    description: str = ""
    deprecated: bool = False
