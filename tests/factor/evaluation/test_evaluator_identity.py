"""Fail-closed identity validation for high-level factor Evaluator."""
from __future__ import annotations

import datetime as dt
import inspect

import polars as pl
import pytest

from tinohelm.factor.evaluation.evaluator import Evaluator
from tinohelm.factor.types import EvalConfig


def test_evaluate_core_validates_identity_uniqueness_before_common_key_join() -> None:
    """Preliminary key alignment must not cartesian-expand duplicate identities."""
    source = inspect.getsource(Evaluator._evaluate_core)
    validation_pos = source.find("_ensure_unique_identity_keys(factor_df")
    join_pos = source.find("factor_df.select(key_cols)")

    assert validation_pos != -1, "_evaluate_core must validate factor identity keys"
    assert validation_pos < join_pos, "identity validation must run before common_keys join"


def test_evaluate_accepts_mixed_datetime_units_for_common_key_join() -> None:
    """Trade-derived panels can be μs while bar-derived return panels are ns."""
    ts_ns = pl.datetime_range(
        start=dt.datetime(2026, 1, 1),
        end=dt.datetime(2026, 1, 2, 15),
        interval="1h",
        eager=True,
    ).cast(pl.Datetime("ns"))
    ts_us = ts_ns.cast(pl.Datetime("us"))
    factor = pl.DataFrame(
        {
            "ts": ts_us,
            "BTC": [float(i) for i in range(len(ts_us))],
            "ETH": [float(len(ts_us) - i) for i in range(len(ts_us))],
        },
        schema={"ts": pl.Datetime("us"), "BTC": pl.Float64, "ETH": pl.Float64},
    )
    fwd = pl.DataFrame(
        {
            "ts": ts_ns,
            "BTC": [0.001 * i for i in range(len(ts_ns))],
            "ETH": [-0.001 * i for i in range(len(ts_ns))],
        },
        schema={"ts": pl.Datetime("ns"), "BTC": pl.Float64, "ETH": pl.Float64},
    )
    config = EvalConfig(
        universe=("BTC", "ETH"),
        start="2026-01-01T00:00:00",
        end="2026-01-02T15:00:00",
        returns_kind="forward_returns",
    )

    result = Evaluator().evaluate(factor, fwd, config)

    assert result.ic_series is not None


def test_evaluate_core_rejects_missing_factor_keys_before_inner_join() -> None:
    """High-level Evaluator must not compute metrics on a biased symbol subset."""
    ts = [dt.datetime(2026, 1, 1) + dt.timedelta(hours=i) for i in range(3)]
    factor = pl.DataFrame({
        "ts": ts,
        "BTC": [1.0, 2.0, 3.0],
        "ETH": [10.0, 20.0, 30.0],
    })
    fwd = pl.DataFrame({"ts": ts, "BTC": [0.01, 0.02, 0.03]})
    config = EvalConfig(
        universe=("BTC", "ETH"),
        start="2026-01-01T00:00:00",
        end="2026-01-01T02:00:00",
        returns_kind="forward_returns",
    )

    with pytest.raises(ValueError, match="missing identity keys"):
        Evaluator().evaluate(factor, fwd, config)
