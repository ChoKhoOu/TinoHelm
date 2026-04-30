"""Walk-forward Purged & Embargoed Cross-Validation.

Implements López de Prado *Advances in Financial Machine Learning* Chapter 7
logic: each fold has a train window followed by an embargo gap then a test
window.  The tail of the train window is also "purged" (removed) to prevent
label leakage from overlapping forward-return windows.

Fold layout (bar indices, all exclusive on the right)
------------------------------------------------------
::

    |<-- train_bars - purge_bars -->||<-- purge_bars -->||<-- embargo_bars -->||<-- test_bars -->|
    train_start          train_end   (discarded)          test_start           test_end

Given ``T`` total bars and a stride of ``step_bars`` (default = ``test_bars``)
the k-th fold is::

    train_start = k * step
    train_end   = train_start + train_bars - purge_bars
    test_start  = train_start + train_bars + embargo_bars
    test_end    = test_start + test_bars

Folds are generated until ``test_end > T``.

Public API
----------
* :class:`Fold` — immutable fold index container
* :func:`generate_folds` — produce fold list from timestamps + spec
* :class:`WalkForwardEvaluator` — run OOS evaluation across folds
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import polars as pl

from tinohelm.factor.types import EvalResult, Panel, WalkForwardSpec


# ---------------------------------------------------------------------------
# Fold dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Fold:
    """Bar-index range for a single walk-forward fold.

    All indices follow Python slice conventions: ``train_start`` is
    inclusive, ``train_end`` / ``test_end`` are exclusive.

    Attributes
    ----------
    train_start:
        First bar index (inclusive) of the purged training window.
    train_end:
        One past the last bar of the training window (exclusive).
        The range ``[train_start, train_end)`` already excludes purged bars.
    test_start:
        First bar index (inclusive) of the test window.
        ``test_start - (train_start + full_train_bars) >= embargo_bars``.
    test_end:
        One past the last bar of the test window (exclusive).
    fold_id:
        Zero-based fold identifier.
    """

    train_start: int
    train_end: int
    test_start: int
    test_end: int
    fold_id: int


# ---------------------------------------------------------------------------
# generate_folds
# ---------------------------------------------------------------------------

def generate_folds(
    timestamps: pl.Series,
    spec: WalkForwardSpec,
) -> list[Fold]:
    """Generate purged & embargoed walk-forward folds.

    Parameters
    ----------
    timestamps:
        The full time-series index (``pl.Series``).  Only the length matters;
        the actual values are used later for panel slicing in
        :class:`WalkForwardEvaluator`.
    spec:
        :class:`~tinohelm.factor.types.WalkForwardSpec` controlling window
        sizes, embargo, purge, and stride.

    Returns
    -------
    list[Fold]
        Ordered list of folds.  May be empty if ``T`` is too small to form
        even a single fold.

    Invariants (per fold)
    ---------------------
    * ``fold.test_start - fold.train_end >= spec.embargo_bars``
    * ``fold.train_end - fold.train_start <= spec.train_bars - spec.purge_bars``
    * ``fold.test_end - fold.test_start == spec.test_bars``
    * ``fold.train_start >= 0`` and ``fold.test_end <= len(timestamps)``
    * ``[fold.train_start, fold.train_end)`` and
      ``[fold.test_start, fold.test_end)`` do not overlap
    """
    T = len(timestamps)
    step = spec.step_bars if spec.step_bars is not None else spec.test_bars
    effective_train = spec.train_bars - spec.purge_bars

    folds: list[Fold] = []
    k = 0
    while True:
        train_start = k * step
        train_end = train_start + effective_train
        # test window begins after the full original train window + embargo
        test_start = train_start + spec.train_bars + spec.embargo_bars
        test_end = test_start + spec.test_bars

        if test_end > T:
            break

        folds.append(
            Fold(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                fold_id=k,
            )
        )
        k += 1

    return folds


# ---------------------------------------------------------------------------
# WalkForwardEvaluator
# ---------------------------------------------------------------------------

class WalkForwardEvaluator:
    """Evaluate a factor across walk-forward folds, returning OOS IC series.

    Parameters
    ----------
    spec:
        Walk-forward configuration (train/test/embargo/purge window sizes).
    """

    def __init__(self, spec: WalkForwardSpec) -> None:
        self.spec = spec

    def evaluate(
        self,
        panel: Panel,
        forward_returns: Panel,
        eval_fn: Callable[[Panel, Panel], EvalResult],
    ) -> EvalResult:
        """Run OOS evaluation across all folds.

        For each fold:

        1. Slice ``panel`` and ``forward_returns`` to the **test** index range.
        2. Call ``eval_fn(test_panel, test_returns)`` to get the OOS result.
        3. Collect ``ic_mean`` / ``ic_std`` / IR (Sharpe of IC) per fold into
           ``EvalResult.oos_ic_series``.

        The returned :class:`EvalResult` summarises across folds:

        * ``ic_mean`` — mean of per-fold OOS ``ic_mean``
        * ``ir`` — mean OOS IC / std OOS IC (across folds)
        * ``oos_ic_series`` — list of per-fold dicts with keys
          ``fold``, ``train_start``, ``train_end``, ``test_start``,
          ``test_end``, ``ic_mean``, ``ic_std``, ``sharpe``

        Parameters
        ----------
        panel:
            Wide panel ``[ts, sym1, sym2, ...]`` of factor scores.
        forward_returns:
            Wide panel ``[ts, sym1, sym2, ...]`` of pre-computed forward
            returns aligned to the same timestamp axis.
        eval_fn:
            Callable that receives ``(factor_panel, returns_panel)`` and
            returns an :class:`EvalResult`.  In production this is
            ``Evaluator()._evaluate_core_panel`` (see :func:`evaluate_full`
            integration). In tests any two-arg callable returning an
            :class:`EvalResult` works.

        Returns
        -------
        EvalResult
            Aggregated result.  ``oos_ic_series`` has one entry per fold.
            ``ic_mean`` / ``ir`` are the cross-fold OOS averages.
        """
        # Extract the timestamp column for fold slicing
        ts_col = "ts"
        if ts_col not in panel.columns:
            raise ValueError(
                f"panel is missing required {ts_col!r} column; got {panel.columns!r}"
            )
        ts_series = panel[ts_col]
        folds = generate_folds(ts_series, self.spec)

        oos_ic_series: list[dict] = []
        warnings: list[dict] = []

        for fold in folds:
            # Slice panel and returns to the test window.
            test_panel = panel.slice(fold.test_start, fold.test_end - fold.test_start)
            if ts_col in forward_returns.columns:
                test_ts = test_panel[ts_col].to_list()
                test_returns = forward_returns.filter(pl.col(ts_col).is_in(test_ts))
            else:
                test_returns = forward_returns.slice(
                    fold.test_start, fold.test_end - fold.test_start
                )

            if test_panel.height == 0 or test_returns.height == 0:
                warning = {
                    "code": "walk_forward_fold_empty",
                    "message": "Walk-forward fold has no aligned OOS rows.",
                    "fold": fold.fold_id,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                }
                warnings.append(warning)
                oos_ic_series.append({
                    "fold": fold.fold_id,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                    "status": "insufficient_data",
                    "ic_mean": None,
                    "ic_std": None,
                    "sharpe": None,
                    "warning_code": "walk_forward_fold_empty",
                })
                continue

            # Run eval on the OOS slice.
            oos_result = eval_fn(test_panel, test_returns)
            if not getattr(oos_result, "ic_series", None) or oos_result.ic_mean is None:
                warning = {
                    "code": "walk_forward_fold_no_valid_ic",
                    "message": "Walk-forward fold had rows but no valid aligned IC observations.",
                    "fold": fold.fold_id,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                }
                warnings.append(warning)
                oos_ic_series.append({
                    "fold": fold.fold_id,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                    "status": "insufficient_data",
                    "ic_mean": None,
                    "ic_std": None,
                    "sharpe": None,
                    "warning_code": "walk_forward_fold_no_valid_ic",
                })
                continue

            # Sharpe of IC = IR across the per-period IC series within this fold.
            ic_std = oos_result.ic_std if oos_result.ic_std else 0.0
            sharpe = (
                oos_result.ic_mean / ic_std
                if ic_std and not (ic_std != ic_std)  # NaN guard
                else 0.0
            )

            oos_ic_series.append(
                {
                    "fold": fold.fold_id,
                    "train_start": fold.train_start,
                    "train_end": fold.train_end,
                    "test_start": fold.test_start,
                    "test_end": fold.test_end,
                    "status": "ok",
                    "ic_mean": float(oos_result.ic_mean) if oos_result.ic_mean is not None else None,
                    "ic_std": float(ic_std),
                    "sharpe": float(sharpe),
                }
            )

        # Aggregate across folds.
        if not oos_ic_series:
            result = EvalResult()
            result.ic_mean = None
            result.ic_std = None
            result.ir = None
            result.ic_tstat = None
            result.oos_ic_series = []
            result.warnings = [{
                "code": "walk_forward_no_folds",
                "message": "Walk-forward configuration produced no folds.",
            }]
            result.walk_forward = {"status": "insufficient_data", "folds": []}
            return result

        valid_fold_rows = [d for d in oos_ic_series if d.get("status") == "ok" and d.get("ic_mean") is not None]
        if not valid_fold_rows:
            result = EvalResult()
            result.ic_mean = None
            result.ic_std = None
            result.ir = None
            result.ic_tstat = None
            result.oos_ic_series = oos_ic_series
            result.warnings = warnings or [{
                "code": "walk_forward_no_valid_folds",
                "message": "Walk-forward produced folds but no valid OOS metrics.",
            }]
            result.walk_forward = {"status": "insufficient_data", "folds": oos_ic_series}
            return result

        ic_means = np.array([d["ic_mean"] for d in valid_fold_rows], dtype=np.float64)
        mean_ic = float(np.mean(ic_means))
        std_ic = float(np.std(ic_means))
        ir = mean_ic / std_ic if std_ic > 1e-12 else 0.0

        result = EvalResult()
        result.ic_mean = mean_ic
        result.ic_std = std_ic
        result.ir = ir
        result.oos_ic_series = oos_ic_series
        result.warnings = warnings
        result.walk_forward = {
            "status": "ok" if not warnings else "partial",
            "folds": oos_ic_series,
            "valid_folds": len(valid_fold_rows),
            "total_folds": len(oos_ic_series),
        }
        return result


__all__ = ["Fold", "WalkForwardEvaluator", "generate_folds"]
