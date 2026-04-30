"""Fail-closed identity validation for high-level factor Evaluator."""
from __future__ import annotations

import inspect

from tinohelm.factor.evaluation.evaluator import Evaluator


def test_evaluate_core_validates_identity_uniqueness_before_common_key_join() -> None:
    """Preliminary key alignment must not cartesian-expand duplicate identities."""
    source = inspect.getsource(Evaluator._evaluate_core)
    validation_pos = source.find("_ensure_unique_identity_keys(factor_df")
    join_pos = source.find("factor_df.select(key_cols)")

    assert validation_pos != -1, "_evaluate_core must validate factor identity keys"
    assert validation_pos < join_pos, "identity validation must run before common_keys join"
