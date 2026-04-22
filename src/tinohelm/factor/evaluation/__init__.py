"""Factor evaluation pipeline.

Structured re-organization of the legacy ``tinohelm.research.{analysis,
robustness, cost}`` modules into domain-specific sub-modules.  Numerical
outputs are bit-for-bit identical to the legacy functions (AC-13.2).

Public API
----------
Orchestration:
    Evaluator

Sub-functions (for direct callers / advanced use):
    compute_ic_series, compute_ic_summary, compute_ic_decay, compute_half_life,
    forward_returns
    compute_quantile_returns
    compute_distribution
    compute_turnover
    compute_rating, rating_letter
    shuffle_test, subsample_ic, cross_symbol_ic, summarize_shuffle_distribution
    edge_waterfall
"""
from tinohelm.factor.evaluation.cost import edge_waterfall
from tinohelm.factor.evaluation.distribution import compute_distribution
from tinohelm.factor.evaluation.evaluator import Evaluator
from tinohelm.factor.evaluation.ic import (
    compute_ic_decay,
    compute_ic_series,
    compute_ic_summary,
    compute_half_life,
    forward_returns,
)
from tinohelm.factor.evaluation.quantile import compute_quantile_returns
from tinohelm.factor.evaluation.rating import compute_rating, rating_letter
from tinohelm.factor.evaluation.robustness import (
    SHUFFLE_MIN_OBSERVATIONS,
    SHUFFLE_SIGNIFICANCE_THRESHOLD,
    cross_symbol_ic,
    shuffle_test,
    subsample_ic,
    summarize_shuffle_distribution,
)
from tinohelm.factor.evaluation.turnover import compute_turnover

__all__ = [
    "Evaluator",
    "SHUFFLE_MIN_OBSERVATIONS",
    "SHUFFLE_SIGNIFICANCE_THRESHOLD",
    "compute_distribution",
    "compute_ic_decay",
    "compute_ic_series",
    "compute_ic_summary",
    "compute_half_life",
    "compute_quantile_returns",
    "compute_rating",
    "compute_turnover",
    "cross_symbol_ic",
    "edge_waterfall",
    "forward_returns",
    "rating_letter",
    "shuffle_test",
    "subsample_ic",
    "summarize_shuffle_distribution",
]
