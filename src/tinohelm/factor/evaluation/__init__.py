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
    correlation_matrix, correlation_matrix_cross_section,
    correlation_matrix_ic_time_series
    hierarchical_cluster, cut_dendrogram
    combine_factors
    orthogonalize, orthogonalize_many
    compare_results, compare_multi
"""
from tinohelm.factor.evaluation.clustering import (
    cut_dendrogram,
    hierarchical_cluster,
)
from tinohelm.factor.evaluation.compare import (
    compare_multi,
    compare_results,
)
from tinohelm.factor.evaluation.composition import combine_factors
from tinohelm.factor.evaluation.correlation import (
    correlation_matrix,
    correlation_matrix_cross_section,
    correlation_matrix_ic_time_series,
)
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
from tinohelm.factor.evaluation.orthogonalize import (
    orthogonalize,
    orthogonalize_many,
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
from tinohelm.factor.evaluation.params_grid import params_grid
from tinohelm.factor.evaluation.segmentation import segment_evaluate
from tinohelm.factor.evaluation.turnover import compute_turnover

__all__ = [
    "Evaluator",
    "SHUFFLE_MIN_OBSERVATIONS",
    "SHUFFLE_SIGNIFICANCE_THRESHOLD",
    "combine_factors",
    "compare_multi",
    "compare_results",
    "compute_distribution",
    "compute_ic_decay",
    "compute_ic_series",
    "compute_ic_summary",
    "compute_half_life",
    "compute_quantile_returns",
    "compute_rating",
    "compute_turnover",
    "correlation_matrix",
    "correlation_matrix_cross_section",
    "correlation_matrix_ic_time_series",
    "cross_symbol_ic",
    "cut_dendrogram",
    "edge_waterfall",
    "forward_returns",
    "hierarchical_cluster",
    "orthogonalize",
    "orthogonalize_many",
    "params_grid",
    "rating_letter",
    "segment_evaluate",
    "shuffle_test",
    "subsample_ic",
    "summarize_shuffle_distribution",
]
