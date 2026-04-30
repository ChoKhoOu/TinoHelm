"""Factor comparison — pairwise metric diff + bootstrap CI, and multi-factor report.

Public API
----------
compare_results(eval_a, eval_b)
    Compare two :class:`~tinohelm.factor.types.EvalResult` objects on key
    metrics (``ic_mean``, ``ir``) using bootstrap resampling to produce
    confidence intervals on the metric differences.

compare_multi(results)
    Produce a multi-factor comparison report: ranking heatmap, rolling-IC
    small multiples, hierarchical dendrogram (via s12 clustering), IC
    time-series cross-correlation, and a structured agent summary dict.

Bootstrap contract
------------------
* Resampling uses :func:`numpy.random.default_rng` (thread-safe).
* ``random_seed=42`` guarantees deterministic output for tests.
* ``n_bootstrap >= 1000`` (default) for reliable CI coverage.

ic_series contract
------------------
``EvalResult.ic_series`` is ``list[dict]`` with entries
``{"date": str, "ic": float}``.  This module extracts the ``"ic"`` floats
before resampling — callers never need to pre-process the series.
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl

from tinohelm.factor.types import EvalResult


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _is_bad_metric_value(val: object) -> bool:
    """Return True if *val* is None, NaN, or infinite."""
    if val is None:
        return True
    try:
        fv = float(val)  # type: ignore[arg-type]
        return math.isnan(fv) or math.isinf(fv)
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Internal helper — extract plain float array from EvalResult.ic_series
# ---------------------------------------------------------------------------

def _extract_ic_values(result: EvalResult) -> np.ndarray:
    """Return a 1-D float64 array of IC values from ``result.ic_series``.

    ``EvalResult.ic_series`` is ``list[dict]`` with entries
    ``{"date": str, "ic": float}``.  Missing or empty series returns an
    empty array.
    """
    raw = result.ic_series or []
    if not raw:
        return np.array([], dtype=np.float64)
    # Each entry is either a dict {"date": ..., "ic": float} or a plain float
    # (defensive: handle both layouts in case the caller pre-processed).
    if isinstance(raw[0], dict):
        return np.array([entry["ic"] for entry in raw], dtype=np.float64)
    return np.array(raw, dtype=np.float64)


def _extract_ic_frame(result: EvalResult, name: str) -> pl.DataFrame:
    """Return ``[date, <name>]`` IC frame for date-aligned correlations."""
    raw = result.ic_series or []
    if not raw:
        return pl.DataFrame(
            {"date": [], name: []},
            schema={"date": pl.Utf8, name: pl.Float64},
        )
    if isinstance(raw[0], dict):
        rows = [
            {"date": str(entry["date"]), name: float(entry["ic"])}
            for entry in raw
            if entry.get("date") is not None
            and not _is_bad_metric_value(entry.get("ic"))
        ]
        return pl.DataFrame(rows, schema={"date": pl.Utf8, name: pl.Float64})
    # Defensive fallback for legacy plain arrays: preserve positional labels so
    # callers with no dates keep the old index-aligned behavior.
    rows = [
        {"date": str(i), name: float(v)}
        for i, v in enumerate(raw)
        if not _is_bad_metric_value(v)
    ]
    return pl.DataFrame(rows, schema={"date": pl.Utf8, name: pl.Float64})


def _paired_ic_arrays(eval_a: EvalResult, eval_b: EvalResult) -> tuple[np.ndarray, np.ndarray]:
    """Return IC arrays aligned by ``date`` for paired A/B comparison."""
    a_frame = _extract_ic_frame(eval_a, "a")
    b_frame = _extract_ic_frame(eval_b, "b")
    if a_frame.height == 0 or b_frame.height == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    paired = a_frame.join(b_frame, on="date", how="inner").drop_nulls()
    if paired.height == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    return (
        paired["a"].to_numpy().astype(np.float64, copy=False),
        paired["b"].to_numpy().astype(np.float64, copy=False),
    )


# ---------------------------------------------------------------------------
# compare_results — pairwise bootstrap CI
# ---------------------------------------------------------------------------

def compare_results(
    eval_a: EvalResult,
    eval_b: EvalResult,
    *,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    random_seed: int = 42,
) -> dict:
    """Compare two EvalResult objects on key metrics with bootstrap CI.

    For each metric (``ic_mean``, ``ir``):

    * ``delta = a - b`` (also exposed as explicit ``a_minus_b`` and
      ``b_minus_a`` fields).
    * A ``n_bootstrap``-iteration bootstrap resamples date-paired IC
      observations with replacement and recomputes the metric difference on
      each sample.
    * The ``(alpha/2, 1 - alpha/2)`` percentile of bootstrap deltas gives
      ``ci_low`` / ``ci_high``.
    * ``direction`` is ``improved`` / ``degraded`` / ``neutral`` from A's
      perspective when the CI excludes zero.

    Parameters
    ----------
    eval_a, eval_b:
        The two evaluation results to compare.
    n_bootstrap:
        Number of bootstrap iterations.  Must be >= 1.
    confidence:
        Confidence level for the interval (e.g. 0.95 = 95% CI).
    random_seed:
        Seed for :func:`numpy.random.default_rng`.  Fixed at 42 by default
        so tests are deterministic.

    Returns
    -------
    dict
        ``{"metric_diffs": [{"name", "a", "b", "delta",
        "a_minus_b", "b_minus_a", "delta_basis", "direction",
        "better_run", "ci_low", "ci_high", "significant"}, ...]}``.
        Values are ``None`` when the metric is undefined or its IC series
        is too short (< 2 points) for bootstrap.
    """
    rng = np.random.default_rng(random_seed)
    # A/B IC observations are naturally paired by date for runs over the same
    # universe/window.  Align on date before bootstrapping so missing buckets do
    # not cause unrelated IC values to be compared positionally.
    a_ic, b_ic = _paired_ic_arrays(eval_a, eval_b)

    diffs: list[dict] = []

    for metric_name in ["ic_mean", "ir"]:
        a_val = getattr(eval_a, metric_name, None)
        b_val = getattr(eval_b, metric_name, None)

        if _is_bad_metric_value(a_val) or _is_bad_metric_value(b_val):
            diffs.append({
                "name": metric_name,
                "a": a_val,
                "b": b_val,
                "delta": None,
                "a_minus_b": None,
                "b_minus_a": None,
                "delta_basis": "a_minus_b",
                "ci_low": None,
                "ci_high": None,
                "significant": "neutral",
                "direction": "neutral",
                "better_run": None,
            })
            continue

        a_val_f = float(a_val)  # type: ignore[arg-type]
        b_val_f = float(b_val)  # type: ignore[arg-type]
        a_minus_b = a_val_f - b_val_f
        b_minus_a = b_val_f - a_val_f

        better_run = "a" if a_minus_b > 0 else "b" if a_minus_b < 0 else None

        # Bootstrap CI — requires at least 2 data points in each series.
        if len(a_ic) < 2 or len(b_ic) < 2:
            diffs.append({
                "name": metric_name,
                "a": a_val_f,
                "b": b_val_f,
                "delta": a_minus_b,
                "a_minus_b": a_minus_b,
                "b_minus_a": b_minus_a,
                "delta_basis": "a_minus_b",
                "ci_low": None,
                "ci_high": None,
                "significant": "neutral",
                "direction": "neutral",
                "better_run": better_run,
            })
            continue

        n = len(a_ic)
        boot_deltas = np.empty(n_bootstrap, dtype=np.float64)

        for k in range(n_bootstrap):
            idx = rng.integers(0, n, size=n)
            a_sample = a_ic[idx]
            b_sample = b_ic[idx]

            if metric_name == "ic_mean":
                bdiff = float(np.mean(a_sample)) - float(np.mean(b_sample))
            else:
                # ir = mean / std — guard against zero std
                a_ir = float(np.mean(a_sample)) / (float(np.std(a_sample, ddof=1)) + 1e-12)
                b_ir = float(np.mean(b_sample)) / (float(np.std(b_sample, ddof=1)) + 1e-12)
                bdiff = a_ir - b_ir

            boot_deltas[k] = bdiff

        alpha = 1.0 - confidence
        ci_low = float(np.percentile(boot_deltas, 100.0 * alpha / 2))
        ci_high = float(np.percentile(boot_deltas, 100.0 * (1.0 - alpha / 2)))

        # Three-state significance: improved / degraded / neutral (AC-1.5.1)
        if ci_low > 0.0:
            significant = "improved"   # a > b significantly under a_minus_b
        elif ci_high < 0.0:
            significant = "degraded"   # a < b significantly under a_minus_b
        else:
            significant = "neutral"    # CI contains zero

        diffs.append({
            "name": metric_name,
            "a": a_val_f,
            "b": b_val_f,
            "delta": a_minus_b,
            "a_minus_b": a_minus_b,
            "b_minus_a": b_minus_a,
            "delta_basis": "a_minus_b",
            "ci_low": ci_low,
            "ci_high": ci_high,
            "significant": significant,
            "direction": significant,
            "better_run": better_run,
        })

    return {"metric_diffs": diffs}


# ---------------------------------------------------------------------------
# _build_agent_summary — structured JSON summary (AC-2.3.1)
# ---------------------------------------------------------------------------

def _build_agent_summary(
    results: dict[str, EvalResult],
    factor_names: list[str],
    values: list[list[float | None]],
) -> dict:
    """Build a structured agent summary dict for LLM/agent consumption.

    Returns
    -------
    dict
        Keys: ``top_performers`` (list), ``warnings`` (list),
        ``regime_sensitivity`` (dict).

    This is parseable as ``jq '.agent_summary.top_performers[0].name'``.
    """
    # --- top performers by IR ---
    valid_by_ir: list[tuple[str, EvalResult]] = [
        (name, r)
        for name, r in results.items()
        if r.ir is not None and not _is_bad_metric_value(r.ir)
    ]
    valid_by_ir.sort(key=lambda x: -float(x[1].ir))

    top_performers: list[dict] = []
    for name, r in valid_by_ir[:3]:
        ir_f = float(r.ir)
        ic_mean_f = float(r.ic_mean)
        ic_consistent = (
            "stable"
            if (ic_mean_f > 0 and ir_f > 0.5) or (ic_mean_f < 0 and ir_f < -0.5)
            else "mixed"
        )
        top_performers.append({
            "name": name,
            "ir": ir_f,
            "why": f"IR={ir_f:.2f} with {ic_consistent} IC sign (mean={ic_mean_f:.4f})",
        })

    # --- warnings: factors with low IR ---
    warnings: list[dict] = []
    for name, r in valid_by_ir:
        if abs(float(r.ir)) < 0.3:
            warnings.append({
                "factor": name,
                "type": "low_ir",
                "message": f"{name} IR={float(r.ir):.2f} below threshold 0.3",
            })

    # --- regime sensitivity from segment_results ---
    regime_sensitivity: dict[str, list[dict]] = {}
    for name, r in results.items():
        if not r.segment_results:
            continue
        for regime_name, segments in r.segment_results.items():
            if not isinstance(segments, dict):
                continue
            if regime_name not in regime_sensitivity:
                regime_sensitivity[regime_name] = []
            for seg_label, seg_result in segments.items():
                if seg_result is None:
                    continue
                seg_ir = getattr(seg_result, "ir", None) if not isinstance(seg_result, dict) else seg_result.get("ir")
                if seg_ir is not None and not _is_bad_metric_value(seg_ir):
                    regime_sensitivity[regime_name].append({
                        "name": name,
                        "segment": seg_label,
                        "ir": float(seg_ir),
                    })

    return {
        "top_performers": top_performers,
        "warnings": warnings,
        "regime_sensitivity": regime_sensitivity,
    }


# ---------------------------------------------------------------------------
# compare_multi — multi-factor report
# ---------------------------------------------------------------------------

def compare_multi(
    results: dict[str, EvalResult],
    *,
    n_bootstrap: int = 1000,
    random_seed: int = 42,
) -> dict:
    """Multi-factor comparison report.

    Produces four data sections plus a plain-English ``agent_summary``:

    1. **ranking_heatmap** — ``(F, M)`` metric table + 1-based rankings per
       metric.  Factors with ``None`` values rank last.
    2. **rolling_ic_small_multiples** — per-factor 30-bar rolling mean of
       the IC time series.  Short series (< 30 bars) are returned as-is.
    3. **dendrogram** — linkage matrix from
       :func:`tinohelm.factor.evaluation.clustering.hierarchical_cluster`
       (Ward method on correlation distance).  Falls back to an empty
       ``linkage_matrix`` when fewer than 2 factors have >= 2 IC
       observations.
    4. **ic_time_series_corr** — pairwise Pearson correlation across IC
       series via
       :func:`tinohelm.factor.evaluation.correlation.correlation_matrix_ic_time_series`.

    Parameters
    ----------
    results:
        Mapping ``{factor_name: EvalResult}``.  At least 2 entries are
        expected; 1-factor input is accepted but the dendrogram section
        will be empty.
    n_bootstrap:
        Passed through to future extensions (not used in this function,
        kept for API consistency with :func:`compare_results`).
    random_seed:
        Reserved for reproducibility (not yet used here).

    Returns
    -------
    dict
        ``{"ranking_heatmap", "rolling_ic_small_multiples", "dendrogram",
        "ic_time_series_corr", "agent_summary"}``.
    """
    from tinohelm.factor.evaluation.correlation import correlation_matrix_ic_time_series
    from tinohelm.factor.evaluation.clustering import hierarchical_cluster

    factor_names = list(results.keys())
    F = len(factor_names)

    _METRICS = ["ic_mean", "ir"]
    _ROLLING_WINDOW = 30

    # ------------------------------------------------------------------ #
    # 1. Ranking heatmap                                                  #
    # ------------------------------------------------------------------ #
    values: list[list[float | None]] = []
    for fname in factor_names:
        row: list[float | None] = []
        for m in _METRICS:
            v = getattr(results[fname], m, None)
            if v is None:
                row.append(None)
            else:
                try:
                    fv = float(v)
                    row.append(None if (fv != fv) else fv)  # drop NaN
                except (TypeError, ValueError):
                    row.append(None)
        values.append(row)

    # 1-based rankings per metric (high → rank 1, None → last rank)
    rankings: list[list[int]] = [[0] * len(_METRICS) for _ in range(F)]
    for m_idx in range(len(_METRICS)):
        col_vals = [(values[i][m_idx], i) for i in range(F)]
        # Separate valid from None; sort valid descending
        valid = sorted(
            [(v, i) for v, i in col_vals if v is not None],
            key=lambda t: -t[0],
        )
        none_indices = [i for v, i in col_vals if v is None]
        # Assign ranks: 1-based for valid, last for None
        for rank, (_, fi) in enumerate(valid, start=1):
            rankings[fi][m_idx] = rank
        last_rank = len(valid) + 1
        for fi in none_indices:
            rankings[fi][m_idx] = last_rank

    ranking_heatmap = {
        "factors": factor_names,
        "metrics": _METRICS,
        "values": values,
        "rankings": rankings,
    }

    # ------------------------------------------------------------------ #
    # 2. Rolling IC small multiples                                       #
    # ------------------------------------------------------------------ #
    series_per_factor: dict[str, list[float]] = {}
    for fname in factor_names:
        ic_arr = _extract_ic_values(results[fname])
        if len(ic_arr) >= _ROLLING_WINDOW:
            rolling = np.array(
                [
                    float(np.mean(ic_arr[max(0, i - _ROLLING_WINDOW + 1): i + 1]))
                    for i in range(len(ic_arr))
                ]
            )
        else:
            rolling = ic_arr.copy()
        # Sanitize NaN → None for JSON serialization
        series_per_factor[fname] = [
            None if (v != v) else float(v) for v in rolling.tolist()
        ]

    rolling_ic_small_multiples = {
        "factors": factor_names,
        "rolling_ic_window": _ROLLING_WINDOW,
        "series": series_per_factor,
    }

    # ------------------------------------------------------------------ #
    # 3. Dendrogram + IC time-series correlation                          #
    # ------------------------------------------------------------------ #
    # Collect per-factor IC frames and align by date before computing time-
    # series correlations.  Positional truncation silently pairs different
    # dates when factors miss different IC buckets.
    ic_frames: dict[str, pl.DataFrame] = {
        fname: _extract_ic_frame(results[fname], fname) for fname in factor_names
    }
    valid_ic_names = [n for n in factor_names if ic_frames[n].height >= 2]

    if len(valid_ic_names) >= 2:
        try:
            aligned = ic_frames[valid_ic_names[0]]
            for name in valid_ic_names[1:]:
                aligned = aligned.join(ic_frames[name], on="date", how="inner")
            aligned_ic: dict[str, pl.Series] = {
                n: aligned[n] for n in valid_ic_names
            } if aligned.height >= 2 else {}
            if not aligned_ic:
                raise ValueError("fewer than 2 common IC dates")
            corr_df = correlation_matrix_ic_time_series(aligned_ic)
            cluster = hierarchical_cluster(corr_df, method="ward")
            dendrogram: dict = {
                "linkage_matrix": cluster["linkage_matrix"].tolist(),
                "labels": cluster["labels"],
            }
            ic_corr_matrix: dict = {
                "factors": valid_ic_names,
                "matrix": (
                    corr_df.drop("factor_name").to_numpy().tolist()
                ),
            }
        except Exception:
            # Defensive: any clustering failure gracefully degrades.
            dendrogram = {"linkage_matrix": [], "labels": factor_names}
            ic_corr_matrix = {
                "factors": factor_names,
                "matrix": [
                    [1.0 if i == j else None for j in range(F)]
                    for i in range(F)
                ],
            }
    else:
        dendrogram = {"linkage_matrix": [], "labels": factor_names}
        ic_corr_matrix = {
            "factors": factor_names,
            "matrix": [
                [1.0 if i == j else None for j in range(F)]
                for i in range(F)
            ],
        }

    # ------------------------------------------------------------------ #
    # 4. Agent summary (structured JSON — AC-2.3.1)                      #
    # ------------------------------------------------------------------ #
    agent_summary = _build_agent_summary(results, factor_names, values)

    return {
        "ranking_heatmap": ranking_heatmap,
        "rolling_ic_small_multiples": rolling_ic_small_multiples,
        "dendrogram": dendrogram,
        "ic_time_series_corr": ic_corr_matrix,
        "agent_summary": agent_summary,
    }


__all__ = [
    "compare_results",
    "compare_multi",
]
