"""Core data types for the declarative factor framework.

Design notes
------------
- All types use ``@dataclass(frozen=True)`` for hashability and immutability.
  The factor framework is pure-Python (no NT dependency), so ``@dataclass``
  is preferred over ``msgspec.Struct`` which is used only in the NT-side code.
- ``Panel`` is a type alias for ``pd.DataFrame`` rather than a wrapper class.
  Wrapping DataFrame adds complexity without benefit for a pure-pandas framework;
  downstream code uses DataFrame's full API directly.  The alias documents
  intent: index = datetime, columns = symbol.
- ``EvalResult`` fields are aligned with ``research/analysis.py``
  ``compute_ic_summary`` + ``compute_half_life`` + ``compute_turnover`` outputs
  (AC-7.2).  Field names are kept identical so s8 can migrate without touching
  existing analysis functions.
- ``code_hash`` in ``FactorSpec`` is a placeholder (computed by s2).  It
  defaults to empty string so s1-only consumers don't need to supply it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Panel — time × symbol DataFrame
# ---------------------------------------------------------------------------

#: Two-dimensional panel: ``index`` is a ``DatetimeIndex``, ``columns`` are
#: symbol strings (e.g. ``"BTCUSDT-PERP"``).  Values are factor scores or
#: price fields, depending on context.
Panel = pd.DataFrame


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
        Expected numpy dtype string (e.g. ``"float64"``).  Used for
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
        Numpy dtype string of the output values.
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
        Tuple of ``InputSpec`` objects describing all required/optional inputs.
    output_spec:
        ``OutputSpec`` describing the produced signal.
    params:
        Default parameter dict (e.g. ``{"lookback": 20}``).  Mutable at
        eval time; this is the baseline.
    version:
        Semantic version string (e.g. ``"1.0.0"``).  Bump on logic changes.
    code_hash:
        SHA-256 hex digest of the factor's source code.  Populated by s2
        (hash_factor task); defaults to empty string until then.
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
    # Factor requires data-layer support not yet implemented; kernel will raise.
    # /api/factor/list filters these out by default.
    experimental: bool = False


# ---------------------------------------------------------------------------
# EvalConfig — evaluation run configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvalConfig:
    """Configuration for a factor evaluation run.

    Attributes
    ----------
    universe:
        List of symbol strings to evaluate against (e.g.
        ``["BTCUSDT-PERP", "ETHUSDT-PERP"]``).
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
        Pandas offset string for IC grouping (``"D"`` = daily, ``"W"`` = weekly).
    log_ret:
        Whether to compute log returns instead of simple returns.
    params:
        Factor parameter overrides applied for this eval run.  Merged on top of
        ``FactorSpec.params`` defaults.
    """

    universe: tuple[str, ...]
    start: str
    end: str
    forward_period: int = 5
    quantiles: int = 5
    cost_bps: float = 4.0
    ic_freq: str = "D"
    log_ret: bool = False
    params: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


# ---------------------------------------------------------------------------
# EvalResult — evaluation output
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    """Results from a factor evaluation run.

    Field names are aligned with ``research/analysis.py`` output to preserve
    numerical consistency (AC-7.2 / AC-13.2):

    - ``ic_mean``, ``ic_std``, ``ir``, ``ic_tstat``, ``ic_positive_pct``,
      ``ic_max_abs`` — from ``compute_ic_summary``
    - ``half_life`` — from ``compute_half_life`` (stored as ``half_life_bars``
      in the raw output; normalized here to ``half_life`` for the data model)
    - ``turnover``, ``turnover_annualized``, ``fee_drag_monthly`` — from
      ``compute_turnover``
    - ``quantile_pnl`` — ``avg_returns`` dict from ``compute_quantile_returns``
    - ``is_monotonic`` — monotonicity flag from ``compute_quantile_returns``

    Extended fields populated by s8 evaluation pipeline (default-empty so s1-only
    consumers are unaffected):

    - ``quantile_cum_returns`` — sampled cumulative return series per quantile
    - ``distribution_stats`` / ``distribution_histogram`` — from ``compute_distribution``
    - ``robustness`` — ``{shuffle, subsample, cross_symbol}`` from
      ``evaluate_full`` only
    - ``cost`` — edge waterfall dict from ``evaluate_full`` only

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

    # Extended fields — populated by the s8 evaluation pipeline.
    # Default to empty so s1-only consumers don't see any schema change.

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
    """

    symbol: str
    field_name: str
    frequency: str
    lookback: int
    source: str = "bar"
