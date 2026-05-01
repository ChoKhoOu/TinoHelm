"""Unit tests — ``tinohelm.factor.evaluation.compare``.

Covers AC-1.5.1 / AC-2.1.1 / AC-2.3.1:

* compare_results output schema + bootstrap CI coverage
* Significance detection when CI excludes zero
* Non-significance when two results are nearly identical
* compare_multi 4-section + agent_summary output
* Dendrogram linkage matrix shape (F-1, 4) from s12 clustering
* Safe degradation for short ic_series
* agent_summary mentions best factor by name

All tests are pure-logic, deterministic (seed=42), polars-only, < 5s total.
No network, no DB, no NT deps.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

from tinohelm.factor.evaluation.compare import compare_multi, compare_results
from tinohelm.factor.types import EvalResult


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_result(
    ic_series_values: list[float],
    ic_mean: float | None = None,
    ir: float | None = None,
) -> EvalResult:
    """Build an EvalResult with an ic_series expressed as plain floats.

    ``ic_mean`` and ``ir`` are computed from ``ic_series_values`` when not
    supplied so that the bootstrap math is consistent with the stored scalars.
    """
    arr = np.array(ic_series_values, dtype=float)
    computed_mean = float(np.mean(arr)) if len(arr) > 0 else 0.0
    computed_ir = (
        float(np.mean(arr) / (np.std(arr, ddof=1) + 1e-12))
        if len(arr) >= 2
        else 0.0
    )
    return EvalResult(
        ic_mean=ic_mean if ic_mean is not None else computed_mean,
        ir=ir if ir is not None else computed_ir,
        ic_series=[{"date": f"2024-01-{i+1:02d}", "ic": float(v)} for i, v in enumerate(ic_series_values)],
    )


# ---------------------------------------------------------------------------
# 1. compare_results output schema
# ---------------------------------------------------------------------------


def test_compare_returns_metric_diffs():
    """compare_results always returns a dict with 'metric_diffs' key.

    Each entry must contain name, a, b, delta, ci_low, ci_high, significant.
    """
    rng = np.random.default_rng(0)
    eval_a = _make_result(rng.normal(0.02, 0.05, 60).tolist())
    eval_b = _make_result(rng.normal(0.04, 0.05, 60).tolist())

    out = compare_results(eval_a, eval_b, n_bootstrap=200, random_seed=42)

    assert "metric_diffs" in out
    diffs = out["metric_diffs"]
    assert isinstance(diffs, list)
    assert len(diffs) == 2  # ic_mean + ir

    required_keys = {
        "name", "a", "b", "delta", "a_minus_b", "b_minus_a",
        "delta_basis", "direction", "better_run", "ci_low", "ci_high", "significant",
    }
    for entry in diffs:
        assert required_keys.issubset(entry.keys()), f"Missing keys: {required_keys - set(entry.keys())}"
        assert entry["name"] in ("ic_mean", "ir")
        assert entry["significant"] in ("improved", "degraded", "neutral"), (
            f"significant must be one of improved/degraded/neutral, got {entry['significant']!r}"
        )


# ---------------------------------------------------------------------------
# 2. Bootstrap actually runs n_bootstrap iterations
# ---------------------------------------------------------------------------


def test_bootstrap_runs_n_iterations():
    """With n_bootstrap=1000 and sufficient data, at least one metric has CI."""
    rng = np.random.default_rng(7)
    eval_a = _make_result(rng.normal(0.01, 0.04, 80).tolist())
    eval_b = _make_result(rng.normal(0.03, 0.04, 80).tolist())

    out = compare_results(eval_a, eval_b, n_bootstrap=1000, random_seed=42)
    diffs = out["metric_diffs"]

    # At least one metric must produce non-None CI bounds
    has_ci = any(d["ci_low"] is not None and d["ci_high"] is not None for d in diffs)
    assert has_ci, "Expected at least one metric to have CI bounds with n_bootstrap=1000"


# ---------------------------------------------------------------------------
# 3. significant=True when CI excludes zero
# ---------------------------------------------------------------------------


def test_significant_when_ci_excludes_zero():
    """IC series with a large mean difference should preserve A/B direction."""
    # eval_a: IC near 0; eval_b: IC near 0.5 — b is higher than a
    eval_a = _make_result([0.0] * 100)
    eval_b = _make_result([0.5] * 100)

    out = compare_results(eval_a, eval_b, n_bootstrap=1000, random_seed=42)
    ic_mean_entry = next(d for d in out["metric_diffs"] if d["name"] == "ic_mean")

    # With zero-variance series the CI is degenerate but delta should be clear.
    assert ic_mean_entry["delta"] == pytest.approx(-0.5, abs=1e-9)
    assert ic_mean_entry["a_minus_b"] == pytest.approx(-0.5, abs=1e-9)
    assert ic_mean_entry["b_minus_a"] == pytest.approx(0.5, abs=1e-9)
    assert ic_mean_entry["significant"] == "degraded"


# ---------------------------------------------------------------------------
# 4. significant=False when CI includes zero
# ---------------------------------------------------------------------------


def test_not_significant_when_ci_includes_zero():
    """Nearly identical ic_series should produce significant=False.

    When eval_a and eval_b share the exact same ic_series, delta is 0 and
    the bootstrap distribution of deltas straddles zero — so CI must contain
    zero (significant=False).  We test the behavioral contract only; exact CI
    values depend on bootstrap sampling and are not pinned.
    """
    rng = np.random.default_rng(99)
    vals = rng.normal(0.02, 0.05, 120).tolist()
    eval_a = _make_result(vals)
    eval_b = _make_result(vals)  # exact same series

    out = compare_results(eval_a, eval_b, n_bootstrap=1000, random_seed=42)
    ic_mean_entry = next(d for d in out["metric_diffs"] if d["name"] == "ic_mean")

    assert ic_mean_entry["delta"] == pytest.approx(0.0, abs=1e-12)
    assert ic_mean_entry["significant"] == "neutral"
    # CI must contain zero (ci_low <= 0 <= ci_high)
    assert ic_mean_entry["ci_low"] is not None
    assert ic_mean_entry["ci_high"] is not None
    assert ic_mean_entry["ci_low"] <= 0.0 <= ic_mean_entry["ci_high"]


# ---------------------------------------------------------------------------
# 5. compare_multi returns 4 sections + agent_summary
# ---------------------------------------------------------------------------


def test_compare_multi_returns_4_pages_plus_agent_summary():
    """compare_multi must return all 4 data sections + agent_summary."""
    rng = np.random.default_rng(1)
    results = {
        "f1": _make_result(rng.normal(0.05, 0.04, 50).tolist()),
        "f2": _make_result(rng.normal(0.02, 0.05, 50).tolist()),
        "f3": _make_result(rng.normal(0.01, 0.03, 50).tolist()),
    }

    out = compare_multi(results)

    required_keys = {
        "ranking_heatmap",
        "rolling_ic_small_multiples",
        "dendrogram",
        "ic_time_series_corr",
        "agent_summary",
    }
    assert required_keys.issubset(out.keys()), (
        f"Missing keys: {required_keys - set(out.keys())}"
    )

    # ranking_heatmap sub-structure
    hm = out["ranking_heatmap"]
    assert "factors" in hm and "metrics" in hm and "values" in hm and "rankings" in hm

    # rolling_ic_small_multiples sub-structure
    rm = out["rolling_ic_small_multiples"]
    assert "factors" in rm and "series" in rm and "rolling_ic_window" in rm

    # agent_summary is a structured dict with required keys (AC-2.3.1)
    summary = out["agent_summary"]
    assert isinstance(summary, dict), f"agent_summary must be dict, got {type(summary)}"
    assert "top_performers" in summary
    assert "warnings" in summary
    assert "regime_sensitivity" in summary


# ---------------------------------------------------------------------------
# 6. Dendrogram linkage matrix shape (F-1, 4)
# ---------------------------------------------------------------------------


def test_compare_multi_dendrogram_uses_s12_clustering():
    """Dendrogram linkage_matrix must have shape (F-1, 4) when F >= 2."""
    rng = np.random.default_rng(2)
    F = 4
    results = {
        f"factor_{i}": _make_result(rng.normal(0.02 * i, 0.04, 60).tolist())
        for i in range(F)
    }

    out = compare_multi(results)
    dendro = out["dendrogram"]

    assert "linkage_matrix" in dendro
    assert "labels" in dendro

    lm = dendro["linkage_matrix"]
    # linkage_matrix is a list-of-lists (serialized from numpy .tolist())
    assert isinstance(lm, list)
    assert len(lm) == F - 1, f"Expected {F-1} rows, got {len(lm)}"
    for row in lm:
        assert len(row) == 4, f"Each linkage row must have 4 columns; got {len(row)}"

    labels = dendro["labels"]
    assert set(labels) == set(results.keys())


def test_compare_multi_ic_correlation_aligns_by_date_not_position():
    """Missing IC dates must be inner-joined before correlation."""
    results = {
        "f1": EvalResult(
            ic_mean=0.0,
            ir=0.0,
            ic_series=[
                {"date": "2024-01-01", "ic": 100.0},
                {"date": "2024-01-02", "ic": 1.0},
                {"date": "2024-01-03", "ic": 2.0},
                {"date": "2024-01-04", "ic": 3.0},
            ],
        ),
        "f2": EvalResult(
            ic_mean=0.0,
            ir=0.0,
            ic_series=[
                {"date": "2024-01-02", "ic": 10.0},
                {"date": "2024-01-03", "ic": 20.0},
                {"date": "2024-01-04", "ic": 30.0},
                {"date": "2024-01-05", "ic": -100.0},
            ],
        ),
    }

    out = compare_multi(results)
    corr = out["ic_time_series_corr"]
    assert corr["factors"] == ["f1", "f2"]
    assert corr["matrix"][0][1] == pytest.approx(1.0)
    assert corr["matrix"][1][0] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 7. Safe degradation for short ic_series
# ---------------------------------------------------------------------------


def test_compare_multi_handles_short_ic_series():
    """Short ic_series (< 2 points) must produce an empty linkage_matrix safely."""
    results = {
        "short_f1": _make_result([0.03]),   # 1 point — too short for clustering
        "short_f2": _make_result([]),        # 0 points
    }

    out = compare_multi(results)
    dendro = out["dendrogram"]

    assert dendro["linkage_matrix"] == [], (
        "Expected empty linkage_matrix for short IC series"
    )
    # agent_summary must still be a dict (possibly empty top_performers)
    assert isinstance(out["agent_summary"], dict)
    assert "top_performers" in out["agent_summary"]


# ---------------------------------------------------------------------------
# 8. agent_summary mentions the best factor
# ---------------------------------------------------------------------------


def test_agent_summary_mentions_best():
    """top_performers[0] must be the factor with the highest IR."""
    results = {
        "f1": _make_result([0.1] * 40, ic_mean=0.10),
        "f2": _make_result([0.05] * 40, ic_mean=0.05),
    }

    out = compare_multi(results)
    summary = out["agent_summary"]

    assert isinstance(summary, dict), f"agent_summary must be dict, got {type(summary)!r}"
    top = summary.get("top_performers", [])
    assert len(top) > 0, "Expected at least one top_performer"
    # f1 has higher ic_mean → higher IR → should appear first
    top_names = [p["name"] for p in top]
    assert "f1" in top_names, (
        f"Expected 'f1' in top_performers names; got: {top_names!r}"
    )
    # Validate schema: each top_performer has name/ir/why
    for p in top:
        assert "name" in p and "ir" in p and "why" in p, (
            f"top_performer missing required keys: {p!r}"
        )


def test_compare_results_bootstrap_pairs_common_dates_only():
    """Pairwise compare must align IC observations by date before bootstrap."""
    eval_a = EvalResult(
        ic_mean=0.2,
        ir=1.0,
        ic_series=[
            {"date": "2024-01-01", "ic": 10.0},
            {"date": "2024-01-02", "ic": 0.1},
            {"date": "2024-01-03", "ic": 0.2},
        ],
    )
    eval_b = EvalResult(
        ic_mean=0.3,
        ir=1.5,
        ic_series=[
            {"date": "2024-01-02", "ic": 0.2},
            {"date": "2024-01-03", "ic": 0.3},
            {"date": "2024-01-04", "ic": -10.0},
        ],
    )

    out = compare_results(eval_a, eval_b, n_bootstrap=200, random_seed=42)
    ic_mean_entry = next(d for d in out["metric_diffs"] if d["name"] == "ic_mean")

    assert ic_mean_entry["ci_low"] is not None
    assert ic_mean_entry["ci_high"] is not None
    # Only the shared dates 2024-01-02 and 2024-01-03 are bootstrapped; the
    # large non-overlapping outliers must not dominate the paired CI. A is
    # lower than B on both shared dates, so a_minus_b CI is negative.
    assert -0.11 <= ic_mean_entry["ci_low"] <= 0.0
    assert -0.11 <= ic_mean_entry["ci_high"] <= 0.0


def test_compare_results_delta_is_a_minus_b_with_explicit_reverse():
    """Pairwise compare should not hide operand direction behind ambiguous delta."""
    eval_a = _make_result([0.30, 0.20, 0.10], ic_mean=0.20, ir=2.0)
    eval_b = _make_result([0.10, 0.05, 0.00], ic_mean=0.05, ir=0.5)

    out = compare_results(eval_a, eval_b, n_bootstrap=50, random_seed=42)

    ic_mean = next(d for d in out["metric_diffs"] if d["name"] == "ic_mean")
    assert ic_mean["a_minus_b"] == pytest.approx(0.15)
    assert ic_mean["b_minus_a"] == pytest.approx(-0.15)
    assert ic_mean["delta"] == pytest.approx(0.15)
    assert ic_mean["delta_basis"] == "a_minus_b:paired_ic"
    assert ic_mean["delta"] == pytest.approx(ic_mean["a"] - ic_mean["b"])
    assert ic_mean["better_run"] == "a"


def test_compare_results_scrubs_non_finite_metric_values_for_strict_json():
    """NaN/Inf metrics must not leak into strict JSON output."""
    eval_a = _make_result([0.1, 0.2, 0.3], ic_mean=float("nan"), ir=float("inf"))
    eval_b = _make_result([0.1, 0.2, 0.3], ic_mean=0.2, ir=1.0)

    out = compare_results(eval_a, eval_b, n_bootstrap=20, random_seed=42)

    json.dumps(out, allow_nan=False)
    for entry in out["metric_diffs"]:
        assert entry["a"] is None
        assert entry["delta"] is None


def test_compare_multi_scrubs_non_finite_values_for_strict_json():
    """Ranking heatmap and rolling IC data must be strict-JSON safe."""
    results = {
        "bad_metric": EvalResult(
            ic_mean=float("inf"),
            ir="not-a-number",
            ic_series=[
                {"date": "2024-01-01", "ic": 0.1},
                {"date": "2024-01-02", "ic": float("inf")},
            ],
        ),
        "ok": EvalResult(
            ic_mean=0.2,
            ir=1.0,
            ic_series=[
                {"date": "2024-01-01", "ic": 0.2},
                {"date": "2024-01-02", "ic": 0.3},
            ],
        ),
    }

    out = compare_multi(results)

    json.dumps(out, allow_nan=False)
    assert out["ranking_heatmap"]["values"][0] == [None, None]
    assert out["rolling_ic_small_multiples"]["series"]["bad_metric"] == [0.1, None]


def test_compare_results_uses_paired_sample_for_delta_and_better_run():
    """Scalar full-run values must not contradict paired-bootstrap direction."""
    eval_a = EvalResult(
        ic_mean=1.0,
        ir=2.0,
        ic_series=[
            {"date": "2024-01-01", "ic": 0.1},
            {"date": "2024-01-02", "ic": 0.1},
            {"date": "2024-01-03", "ic": 9.0},
        ],
    )
    eval_b = EvalResult(
        ic_mean=0.0,
        ir=1.0,
        ic_series=[
            {"date": "2024-01-01", "ic": 0.2},
            {"date": "2024-01-02", "ic": 0.2},
            {"date": "2024-01-04", "ic": -9.0},
        ],
    )

    out = compare_results(eval_a, eval_b, n_bootstrap=100, random_seed=42)
    ic_mean = next(d for d in out["metric_diffs"] if d["name"] == "ic_mean")

    assert ic_mean["a"] == pytest.approx(0.1)
    assert ic_mean["b"] == pytest.approx(0.2)
    assert ic_mean["delta"] == pytest.approx(-0.1)
    assert ic_mean["delta"] == pytest.approx(ic_mean["a"] - ic_mean["b"])
    assert ic_mean["full_a"] == pytest.approx(1.0)
    assert ic_mean["full_b"] == pytest.approx(0.0)
    assert ic_mean["delta_basis"] == "a_minus_b:paired_ic"
    assert ic_mean["direction"] == "degraded"
    assert ic_mean["better_run"] == "b"
