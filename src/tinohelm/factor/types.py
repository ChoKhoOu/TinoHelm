"""Core data types for the declarative factor framework.

Design notes
------------
- All types use ``@dataclass(frozen=True)`` for hashability and immutability
  (except :class:`EvalResult`, which is mutable so callers can populate
  fields incrementally during evaluation).  The factor framework is pure
  Python (no NT dependency), so ``@dataclass`` is preferred over
  ``msgspec.Struct`` which is used only in the NT-side code.
- :data:`Panel` is a type alias for ``pl.DataFrame`` rather than a wrapper
  class.  As of the polars migration the canonical wide-table layout is::

      column ``ts``      (Datetime, length T)
      columns symbol₁..N (Float64, factor scores or price fields)

  Wrapping the DataFrame adds complexity without benefit; downstream code
  uses polars' full API directly.
- :class:`EvalResult` fields are aligned with ``research/analysis.py``
  ``compute_ic_summary`` + ``compute_half_life`` + ``compute_turnover``
  outputs.  Field names are kept identical so downstream code can migrate
  without touching existing analysis functions.
- ``code_hash`` in :class:`FactorSpec` is a placeholder (computed by the
  hash-factor task).  It defaults to empty string so consumers don't need
  to supply it.

Walk-forward / segmentation extensions
--------------------------------------
:class:`WalkForwardSpec` and the new :class:`EvalConfig` fields
(``universe_id``, ``neutralize``, ``walk_forward``, ``segments``) drive
the López-de-Prado-style purged & embargoed CV pipeline introduced in
the factor-framework rebuild (see ``3-tech-design.md`` §3.7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl


# ---------------------------------------------------------------------------
# Panel — wide DataFrame indexed by ``ts`` plus N symbol columns
# ---------------------------------------------------------------------------

#: Two-dimensional wide panel.  Layout: column ``ts`` (Datetime) plus N
#: symbol columns (e.g. ``"BTCUSDT-PERP"``, ``"ETHUSDT-PERP"``).  Values are
#: factor scores or price fields, depending on context.
Panel = pl.DataFrame


# ---------------------------------------------------------------------------
# InputSpec — describes one input field consumed by a factor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InputSpec:
    """Specification for a single input field required by a factor.

    Attributes
    ----------
    field_name:
        Canonical field name after alias resolution.  Examples: ``"close"``,
        ``"volume"``, ``"funding_rate"``, ``"open_interest"``.
    frequency:
        Bar/tick frequency string.  Follows NT bar-type convention for bars
        (e.g. ``"1-MINUTE"``, ``"5-MINUTE"``), or ``"tick"`` / ``"8h"`` for
        non-bar sources.  ``None`` means the factor infers frequency from
        the evaluation config.
    dtype:
        Expected numpy/polars dtype string (e.g. ``"float64"``).  Used for
        validation only; not enforced at runtime.
    required:
        Whether the field is mandatory.  Optional inputs can be ``None``
        when loading data.
    """

    field_name: str
    frequency: str | None = None
    dtype: str = "float64"
    required: bool = True


# ---------------------------------------------------------------------------
# OutputSpec — describes the output signal produced by a factor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutputSpec:
    """Specification for the signal output produced by a factor.

    Attributes
    ----------
    dtype:
        Numpy/polars dtype string of the output values.
    value_range:
        Optional ``(min, max)`` tuple documenting the expected output range.
        ``None`` means unbounded.  Example: ``(-1.0, 1.0)`` for a z-scored
        factor, ``(0.0, 100.0)`` for RSI.
    description:
        Human-readable description of what the signal represents.
    """

    dtype: str = "float64"
    value_range: tuple[float, float] | None = None
    description: str = ""


# ---------------------------------------------------------------------------
# FactorSpec — full declarative specification of a factor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FactorSpec:
    """Declarative specification for a factor.

    This is the single source of truth that drives registration, data loading,
    evaluation, and versioning for a factor.

    Attributes
    ----------
    name:
        Unique factor identifier (e.g. ``"ret_N"``, ``"vwap_dev"``).
    category:
        Semantic category label (e.g. ``"动量"``, ``"波动"``, ``"量价"``).
    description:
        Human-readable description of the factor's logic.
    lookback:
        Default lookback window in bars.  Must be >= 1.  Determines how much
        history must be loaded before the first valid output.
    input_specs:
        Tuple of :class:`InputSpec` objects describing all required/optional
        inputs.
    output_spec:
        :class:`OutputSpec` describing the produced signal.
    params:
        Default parameter dict (e.g. ``{"lookback": 20}``).  Mutable at
        eval time; this is the baseline.
    version:
        Semantic version string (e.g. ``"1.0.0"``).  Bump on logic changes.
    code_hash:
        SHA-256 hex digest of the factor's source code.  Populated by the
        hash-factor task; defaults to empty string until then.
    needs_backend:
        Whether the kernel relies on an :class:`AbstractBackend` injected
        operator set (vs. pure ``polars`` expressions).
    experimental:
        Factor requires data-layer support not yet implemented; kernel will
        raise.  ``/api/factor/list`` filters these out by default.
    deprecated:
        Factor is being phased out.  Kept registerable for backwards-
        compatible re-runs of historical eval results, but hidden from the
        default factor catalogue and excluded from auto-generated multi-
        factor reports.  Independent of :attr:`experimental`.
    signal_compatible:
        Whether the factor's output can be consumed directly by a
        :class:`~tinohelm.signal.types.SignalSpec` kernel.  ``False`` means
        the factor is research-only — exposed in the evaluation UI but never
        offered as a signal source.
    """

    name: str
    category: str
    description: str = ""
    lookback: int = 1
    input_specs: tuple[InputSpec, ...] = field(default_factory=tuple)
    output_spec: OutputSpec = field(default_factory=OutputSpec)
    params: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    version: str = "1.0.0"
    code_hash: str = ""
    needs_backend: bool = False
    experimental: bool = False
    deprecated: bool = False
    signal_compatible: bool = True


# ---------------------------------------------------------------------------
# WalkForwardSpec — purged & embargoed walk-forward split configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalkForwardSpec:
    """Configuration for López-de-Prado-style walk-forward CV folds.

    Each fold defines an in-sample (train) window followed by an
    out-of-sample (test) window, with optional ``purge`` (rows dropped
    from the tail of the train window to prevent leakage from labels
    overlapping the test window) and ``embargo`` (gap between train and
    test) buffers.

    Attributes
    ----------
    train_bars:
        Number of bars in each train window.  Must be >= 1.
    test_bars:
        Number of bars in each test window.  Must be >= 1.
    embargo_bars:
        Number of bars to leave blank between the end of train and the
        start of test (the López-de-Prado *embargo*).  Default 0.
    purge_bars:
        Number of bars to remove from the tail of the train window
        (the López-de-Prado *purge*).  Default 0.
    step_bars:
        Stride between consecutive folds, in bars.  ``None`` (default)
        means use ``test_bars`` so folds do not overlap.
    """

    train_bars: int
    test_bars: int
    embargo_bars: int = 0
    purge_bars: int = 0
    step_bars: int | None = None


# ---------------------------------------------------------------------------
# EvalConfig — evaluation run configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvalConfig:
    """Configuration for a factor evaluation run.

    Attributes
    ----------
    universe:
        Tuple of symbol strings to evaluate against (e.g.
        ``("BTCUSDT-PERP", "ETHUSDT-PERP")``).
    start:
        Evaluation window start date/time string (ISO-8601).
    end:
        Evaluation window end date/time string (ISO-8601).
    forward_period:
        Forward return horizon in bars.  Default 5.
    quantiles:
        Number of quantile buckets for quantile-return analysis.  Default 5.
    cost_bps:
        Round-trip transaction cost in basis points.  Used by cost analysis.
    ic_freq:
        Pandas-compatible offset alias for IC grouping (``"D"`` = daily,
        ``"W"`` = weekly).  Mapped to polars ``group_by_dynamic`` ``every``
        argument by the IC evaluator.
    log_ret:
        Whether to compute log returns instead of simple returns.
    returns_kind:
        Explicit semantic type for the ``returns`` argument passed to
        :class:`~tinohelm.factor.evaluation.evaluator.Evaluator`.  ``"close"``
        means the evaluator derives forward returns from raw close prices;
        ``"forward_returns"`` means callers already supplied a pre-shifted
        forward-return panel.  This must be explicit because all-positive
        return windows are indistinguishable from prices by value shape alone.
    params:
        Factor parameter overrides applied for this eval run.  Merged on top
        of :attr:`FactorSpec.params` defaults.
    universe_id:
        Reference into the ``universes`` table — when present, the universe
        snapshot tied to this id is the authoritative cell mask (PIT-aware).
        ``None`` uses the inline :attr:`universe` symbol tuple.
    neutralize:
        Tuple of :class:`~tinohelm.aligner.exposure.ExposureProvider` names
        applied as cross-section regression residuals before evaluation.
        Empty tuple means no neutralization.  Names are resolved by
        ``aligner/registry.resolve``.
    walk_forward:
        Optional :class:`WalkForwardSpec` driving purged & embargoed CV.
        ``None`` runs the evaluator on the full window without folds.
    segments:
        Tuple of segmentation provider names (e.g. ``("btc_trend",
        "vol_regime", "funding_level")``).  Each provider produces a
        partition of timestamps which the evaluator slices through to
        produce per-regime IC/PnL summaries.
    """

    universe: tuple[str, ...]
    start: str
    end: str
    forward_period: int = 5
    quantiles: int = 5
    cost_bps: float = 4.0
    ic_freq: str = "D"
    log_ret: bool = False
    returns_kind: Literal["close", "forward_returns"] = "close"
    params: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    universe_id: int | None = None
    neutralize: tuple[str, ...] = ()
    walk_forward: WalkForwardSpec | None = None
    segments: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# EvalResult — evaluation output
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Results from a factor evaluation run.

    Field names are aligned with ``research/analysis.py`` output to preserve
    numerical consistency:

    - ``ic_mean``, ``ic_std``, ``ir``, ``ic_tstat``, ``ic_positive_pct``,
      ``ic_max_abs`` — from ``compute_ic_summary``
    - ``half_life`` — from ``compute_half_life`` (stored as ``half_life_bars``
      in the raw output; normalized here to ``half_life`` for the data model)
    - ``turnover``, ``turnover_annualized``, ``fee_drag_monthly`` — from
      ``compute_turnover``
    - ``quantile_pnl`` — ``avg_returns`` dict from ``compute_quantile_returns``
    - ``is_monotonic`` — monotonicity flag from ``compute_quantile_returns``

    Extended fields populated by the evaluation pipeline (default-empty so
    consumers without the new analytics still see a valid result):

    - ``quantile_cum_returns`` — sampled cumulative return series per quantile
    - ``distribution_stats`` / ``distribution_histogram`` — from
      ``compute_distribution``
    - ``robustness`` — ``{shuffle, subsample, cross_symbol}`` from
      ``evaluate_full`` only
    - ``cost`` — edge waterfall dict from ``evaluate_full`` only
    - ``oos_ic_series`` — per-fold IC summaries from
      :class:`WalkForwardEvaluator` (each element is a dict with ``fold``,
      ``train_start``, ``train_end``, ``test_start``, ``test_end``,
      ``ic_mean``, ``ic_std``, ``sharpe``)
    - ``segment_results`` — per-segment IC/PnL summaries keyed by segment
      provider name, e.g. ``{"btc_trend": {"up": {...}, "down": {...}}}``
    - ``neutralization_config`` — JSON-serialisable record of the providers
      and parameters used for neutralization (e.g.
      ``{"providers": ["btc_beta"], "rolling_window": 60, "method": "ols"}``)
    - ``baseline_id`` — when comparing against a baseline run, the
      ``factor_runs.id`` of the baseline (in-memory only; persisted via the
      separate ``factor_runs.baseline_id`` column).

    Non-finite floats (NaN / Infinity) must never appear in any field — use
    ``None`` for undefined numeric values so PostgreSQL JSON columns don't
    reject the payload (see ``CLAUDE.md`` Pitfalls → NaN/Infinity).

    All numeric fields default to ``0.0`` / ``None`` / empty dict so callers
    can construct a result object before populating it incrementally.
    """

    # IC stats
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ir: float = 0.0
    ic_tstat: float = 0.0
    ic_positive_pct: float = 0.0
    ic_max_abs: float = 0.0

    # Decay / half-life
    half_life: int | None = None

    # Quantile analysis
    quantile_pnl: dict[str, float] = field(default_factory=dict)
    is_monotonic: bool = False

    # Turnover / cost
    turnover: float = 0.0
    turnover_annualized: float = 0.0
    fee_drag_monthly: float = 0.0

    # Rating (0-3 scale, same as compute_rating)
    rating: int = 0

    # IC time series (list of {"date": str, "ic": float})
    ic_series: list[dict] = field(default_factory=list)

    # IC decay curve (list of {"lag": int, "ic": float})
    ic_decay: list[dict] = field(default_factory=list)

    # Extended fields — populated by the evaluation pipeline.
    # Default to empty so existing consumers don't see any schema change.

    # Per-quantile cumulative return series (sampled) — list per Q label.
    quantile_cum_returns: dict[str, list[dict]] = field(default_factory=dict)

    # Factor distribution statistics (mean/std/skew/kurt/...).
    distribution_stats: dict[str, float] = field(default_factory=dict)

    # Factor distribution histogram (list of {"bin_start", "bin_end", "count"}).
    distribution_histogram: list[dict] = field(default_factory=list)

    # Robustness checks (populated by ``evaluate_full`` only).
    # Shape: {"shuffle": {...}, "subsample": [...], "cross_symbol": [...]}
    robustness: dict[str, Any] = field(default_factory=dict)

    # Cost/edge waterfall (populated by ``evaluate_full`` only).
    cost: dict[str, float] = field(default_factory=dict)

    # Walk-forward / neutralization extensions.

    #: Per-fold IC summary list produced by :class:`WalkForwardEvaluator`.
    #: Each element is a dict with keys ``fold``, ``train_start``,
    #: ``train_end``, ``test_start``, ``test_end``, ``ic_mean``, ``ic_std``,
    #: ``sharpe``.  Empty when :attr:`EvalConfig.walk_forward` is ``None``.
    oos_ic_series: list[dict] = field(default_factory=list)

    #: Per-segment evaluation results keyed by segment provider name.  Each
    #: value is a mapping ``{segment_label: {metric: value, ...}}``.  Empty
    #: when :attr:`EvalConfig.segments` is empty.
    segment_results: dict[str, dict] = field(default_factory=dict)

    #: JSON-serialisable record of the neutralization configuration applied
    #: during evaluation.  Empty when no neutralization was requested.
    neutralization_config: dict[str, Any] = field(default_factory=dict)

    #: Optional baseline run id used for in-memory comparison only — not
    #: persisted on the result row itself (the dedicated
    #: ``factor_runs.baseline_id`` column carries this).
    baseline_id: str | None = None


# ---------------------------------------------------------------------------
# DataRequest — specifies what data a factor needs loaded
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DataRequest:
    """Describes a data loading request for one factor input.

    Used by the data layer to fetch the right data before factor computation.

    Attributes
    ----------
    symbol:
        Instrument symbol (e.g. ``"BTCUSDT-PERP"``).
    field_name:
        Canonical field name (post alias resolution).
    frequency:
        Bar frequency or ``"tick"`` / ``"8h"`` for non-bar sources.
    lookback:
        Number of extra bars to load before ``start`` for warmup.
    source:
        Data source type: ``"bar"``, ``"funding_rate"``, ``"trade_tick"``,
        ``"quote_tick"``.  Defaults to ``"bar"`` for OHLCV fields.
    source_type:
        Optional physical catalog source under a logical source.  For
        ``source="bar"`` this is the Binance kline-family source type such as
        ``"klines"`` or ``"markPriceKlines"``.  ``None`` means the default
        trade kline source (``"klines"``).
    """

    symbol: str
    field_name: str
    frequency: str
    lookback: int
    source: str = "bar"
    source_type: str | None = None
