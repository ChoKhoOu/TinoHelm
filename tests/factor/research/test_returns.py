import numpy as np
import pytest

from tinohelm.factor.research.panel import MatrixPanel
from tinohelm.factor.research.returns import ForwardReturnsStore, compute_forward_returns_matrix


def _panel(values):
    return MatrixPanel(
        ts=np.array(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"], dtype="datetime64[ns]"),
        symbols=("BTCUSDT", "ETHUSDT"),
        values=np.array(values, dtype=float),
    )


def test_forward_returns_align_to_current_timestamp_and_tail_nan():
    close = _panel([[10, 20], [11, 18], [22, 36], [44, 18]])

    result = compute_forward_returns_matrix(close, 2)

    np.testing.assert_allclose(result.values[:2], [[1.2, 0.8], [3.0, 0.0]])
    assert np.isnan(result.values[2:]).all()
    np.testing.assert_array_equal(result.ts, close.ts)


def test_period_must_be_positive():
    with pytest.raises(ValueError, match="> 0"):
        compute_forward_returns_matrix(_panel([[1, 1], [2, 2], [3, 3], [4, 4]]), 0)


def test_zero_or_non_finite_pair_emits_nan():
    close = _panel([[0, 1], [2, np.inf], [4, 8], [8, 16]])

    result = compute_forward_returns_matrix(close, 1)

    assert np.isnan(result.values[0, 0])
    assert np.isnan(result.values[0, 1])
    assert np.isnan(result.values[1, 1])


def test_forward_returns_emit_nan_across_timestamp_gaps():
    close = MatrixPanel(
        ts=np.array(["2024-01-01", "2024-01-02", "2024-01-04", "2024-01-05"], dtype="datetime64[ns]"),
        symbols=("BTCUSDT",),
        values=np.array([[1.0], [2.0], [4.0], [8.0]]),
    )

    result = compute_forward_returns_matrix(close, 1, expected_step_ns=24 * 60 * 60 * 1_000_000_000)

    np.testing.assert_allclose(result.values[:, 0], [1.0, np.nan, 1.0, np.nan], equal_nan=True)


def test_forward_returns_require_expected_step_for_irregular_timestamps():
    close = MatrixPanel(
        ts=np.array(["2024-01-01", "2024-01-02", "2024-01-04"], dtype="datetime64[ns]"),
        symbols=("BTCUSDT",),
        values=np.array([[1.0], [2.0], [4.0]]),
    )

    with pytest.raises(ValueError, match="expected_step_ns"):
        compute_forward_returns_matrix(close, 1)


def test_forward_returns_store_returns_same_cached_object_for_same_instance():
    close = _panel([[1, 1], [2, 2], [3, 3], [4, 4]])
    store = ForwardReturnsStore()

    first = store.get_or_compute(close, [1, 2])
    second = store.get_or_compute(close, [2, 1, 2])

    assert first is second
    assert first[1] is second[1]


def test_forward_returns_store_normalizes_inferred_and_explicit_step_key():
    close = _panel([[1, 1], [2, 2], [3, 3], [4, 4]])
    store = ForwardReturnsStore()
    one_day_ns = 24 * 60 * 60 * 1_000_000_000

    inferred = store.get_or_compute(close, [1])
    explicit = store.get_or_compute(close, [1], expected_step_ns=one_day_ns)

    assert inferred is explicit


def test_forward_returns_store_outputs_are_read_only():
    close = _panel([[1, 1], [2, 2], [3, 3], [4, 4]])
    store = ForwardReturnsStore()

    returns = store.get_or_compute(close, [1])

    with pytest.raises(ValueError, match="read-only"):
        returns[1].values[0, 0] = 99.0
    with pytest.raises(TypeError):
        returns[99] = returns[1]  # type: ignore[index]

    cached = store.get_or_compute(close, [1])
    assert set(cached) == {1}


def test_forward_returns_store_uses_content_key_across_equal_panels():
    close1 = _panel([[1, 1], [2, 2], [3, 3], [4, 4]])
    close2 = _panel([[1, 1], [2, 2], [3, 3], [4, 4]])
    store = ForwardReturnsStore()

    first = store.get_or_compute(close1, [1])
    second = store.get_or_compute(close2, [1])

    assert first is second
    assert first[1] is second[1]


def test_forward_returns_content_key_canonicalizes_nan_payload_bits():
    nan_a = np.array([0x7FF8000000000001], dtype=np.uint64).view(np.float64)[0]
    nan_b = np.array([0x7FF8000000000002], dtype=np.uint64).view(np.float64)[0]
    close1 = _panel([[1, nan_a], [2, 2], [3, 3], [4, 4]])
    close2 = _panel([[1, nan_b], [2, 2], [3, 3], [4, 4]])
    store = ForwardReturnsStore()

    first = store.get_or_compute(close1, [1])
    second = store.get_or_compute(close2, [1])

    assert first is second


def test_forward_returns_store_binds_explicit_close_key_to_content():
    close1 = _panel([[1, 1], [2, 2], [3, 3], [4, 4]])
    close2 = _panel([[10, 10], [30, 10], [60, 10], [120, 10]])
    store = ForwardReturnsStore()

    first = store.get_or_compute(close1, [1], close_key="close-v1")
    second = store.get_or_compute(close2, [1], close_key="close-v1")

    assert first is not second
    np.testing.assert_allclose(first[1].values[0], [1.0, 1.0])
    np.testing.assert_allclose(second[1].values[0], [2.0, 0.0])
