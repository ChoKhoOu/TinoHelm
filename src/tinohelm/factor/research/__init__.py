"""Research-native factor evaluation primitives."""

from tinohelm.factor.research.batch import BatchFactorEvaluator
from tinohelm.factor.research.matrix_eval import (
    compute_ic_matrix,
    rank_rows,
    rowwise_corr,
    summarize_ic_matrix,
)
from tinohelm.factor.research.panel import (
    CanonicalBars,
    MatrixPanel,
    assert_unique_ts_symbol,
    canonicalize_long_bars,
    long_to_wide_panels,
    matrix_to_wide,
    wide_to_matrix,
)
from tinohelm.factor.research.reader import ResearchDataRequest, ResearchParquetReader
from tinohelm.factor.research.returns import (
    ForwardReturnsKey,
    ForwardReturnsStore,
    compute_forward_returns_matrix,
)

__all__ = [
    "BatchFactorEvaluator",
    "CanonicalBars",
    "ForwardReturnsKey",
    "ForwardReturnsStore",
    "MatrixPanel",
    "ResearchDataRequest",
    "ResearchParquetReader",
    "assert_unique_ts_symbol",
    "canonicalize_long_bars",
    "compute_forward_returns_matrix",
    "compute_ic_matrix",
    "long_to_wide_panels",
    "matrix_to_wide",
    "rank_rows",
    "rowwise_corr",
    "summarize_ic_matrix",
    "wide_to_matrix",
]
