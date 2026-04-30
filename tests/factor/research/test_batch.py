import numpy as np

from tinohelm.factor.research.batch import BatchFactorEvaluator
from tinohelm.factor.research.panel import MatrixPanel
from tinohelm.factor.research.returns import ForwardReturnsStore


class CountingStore(ForwardReturnsStore):
    def __init__(self):
        super().__init__()
        self.calls = 0
        self.log_ret_values: list[bool] = []

    def get_or_compute(self, close, periods, log_ret=False, close_key=None, expected_step_ns=None):
        self.calls += 1
        self.log_ret_values.append(log_ret)
        return super().get_or_compute(
            close,
            periods,
            log_ret=log_ret,
            close_key=close_key,
            expected_step_ns=expected_step_ns,
        )


def _panel(values):
    rows = len(values)
    return MatrixPanel(
        ts=np.array([f"2024-01-{day:02d}" for day in range(1, rows + 1)], dtype="datetime64[ns]"),
        symbols=("a", "b", "c", "d"),
        values=np.array(values, dtype=float),
    )


def test_batch_evaluator_reuses_forward_returns_for_factors():
    close = _panel([
        [1, 1, 1, 1],
        [2, 3, 4, 5],
        [3, 5, 7, 9],
        [4, 7, 10, 13],
    ])
    factors = {
        "trend": _panel([[1, 2, 3, 4]] * 4),
        "inverse": _panel([[4, 3, 2, 1]] * 4),
    }
    store = CountingStore()

    result = BatchFactorEvaluator(store, min_valid=4).evaluate_ic(
        factors,
        close,
        [1],
        freq=None,
        min_total_pairs=0,
    )

    assert store.calls == 1
    assert store.log_ret_values == [False]
    assert set(result) == {"trend", "inverse"}
    assert set(result["trend"]) == {1}
    assert result["trend"][1]["ic_mean"] > 0
    assert result["inverse"][1]["ic_mean"] < 0


def test_batch_evaluator_passes_log_return_mode_to_shared_store():
    close = _panel([[1, 1, 1, 1], [2, 3, 4, 5], [3, 5, 7, 9], [4, 7, 10, 13]])
    factors = {"trend": _panel([[1, 2, 3, 4]] * 4)}
    store = CountingStore()

    BatchFactorEvaluator(store, min_valid=4).evaluate_ic(
        factors,
        close,
        [1],
        log_ret=True,
        freq=None,
        min_total_pairs=0,
    )

    assert store.log_ret_values == [True]
