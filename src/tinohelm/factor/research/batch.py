"""Batch factor evaluation with shared forward returns."""
from __future__ import annotations

from typing import Mapping, Sequence, cast

from tinohelm.factor.research.matrix_eval import Method, compute_ic_matrix, summarize_ic_matrix
from tinohelm.factor.research.panel import MatrixPanel
from tinohelm.factor.research.returns import ForwardReturnsStore



class BatchFactorEvaluator:
    """Evaluate many factor matrices against shared forward returns."""

    def __init__(self, returns_store: ForwardReturnsStore | None = None, min_valid: int = 20) -> None:
        self.returns_store = returns_store or ForwardReturnsStore()
        self.min_valid = min_valid

    def evaluate_ic(
        self,
        factors: Mapping[str, MatrixPanel],
        close: MatrixPanel,
        periods: Sequence[int],
        method: str = "spearman",
        log_ret: bool = False,
        expected_step_ns: int | None = None,
        freq: str | None = "D",
        min_total_pairs: int = 30,
    ) -> dict[str, dict[int, dict[str, float]]]:
        if method not in ("spearman", "pearson"):
            raise ValueError("method must be 'spearman' or 'pearson'")
        forward = self.returns_store.get_or_compute(
            close,
            periods,
            log_ret=log_ret,
            expected_step_ns=expected_step_ns,
        )
        output: dict[str, dict[int, dict[str, float]]] = {}
        for name, factor in factors.items():
            per_period: dict[int, dict[str, float]] = {}
            for period, returns in forward.items():
                ic_series = compute_ic_matrix(
                    factor,
                    returns,
                    method=cast(Method, method),
                    min_valid=self.min_valid,
                    freq=freq,
                    min_total_pairs=min_total_pairs,
                )
                per_period[period] = summarize_ic_matrix(ic_series)
            output[name] = per_period
        return output
