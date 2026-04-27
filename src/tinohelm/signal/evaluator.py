"""SignalEvaluator — evaluate a weight panel against future returns.

Pure logic module: no nautilus_trader dependency.

The evaluator takes a weight panel, a future-returns panel, and a
:class:`~tinohelm.signal.types.CostModel` and produces a
:class:`SignalEvalResult` dataclass containing the canonical set of
signal-quality metrics (Sharpe, MDD, turnover, capacity, tail-loss, PnL
curves).

Design notes
------------
- All arithmetic is NumPy-only for speed. Polars is used only for the
  inner-join alignment step (``weight_panel`` and ``future_returns`` are
  both ``pl.DataFrame`` with a ``"ts"`` column).
- NaN weights are treated as 0 contributions via ``np.nansum``/
  ``np.nanmean``; the capacity loop skips all-NaN timestamps.
- Turnover convention: *single-sided* (0.5 × Σ|Δw|). The initial
  period uses Σ|w[0]| (entry from flat).
- Cost rate (fractional): ``(fee_bps_per_side + slippage_bps_per_side
  - rebate_bps_per_side) / 10_000``. This matches ``CostModel``'s
  documented total-per-side formula.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from tinohelm.signal.types import CostModel

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalEvalResult:
    """Result of evaluating a signal on historical data.

    Attributes
    ----------
    sharpe:
        Annualised Sharpe ratio of *net* period returns.
    mdd:
        Maximum drawdown of the cumulative net PnL curve (expressed as a
        positive fraction, e.g. ``0.15`` for 15%).
    turnover_annualized:
        Mean single-sided turnover per period × ``periods_per_year``.
    capacity_score:
        Portfolio concentration proxy in ``[0, 1]``.  Computed as
        ``1 - mean(top1_weight / gross_weight_per_period)``.  A fully
        concentrated (top-1 = 100%) signal has ``capacity_score=0``; a
        perfectly diversified 4-stock equal-weight signal has
        ``capacity_score=0.75``.
    tail_loss_p99:
        1st-percentile net period return (worst 1%).  Usually negative.
    net_pnl_curve:
        Cumulative sum of net period returns (length == ``n_periods``).
    gross_pnl_curve:
        Cumulative sum of gross period returns before cost deduction
        (length == ``n_periods``).
    total_return:
        Last value of ``net_pnl_curve``; 0 if ``n_periods == 0``.
    n_periods:
        Number of timestamp rows evaluated (after inner-join alignment).
    cost_drag:
        Total cost drag = Σ(cost per period) = gross_total - net_total.
    """

    sharpe: float
    mdd: float
    turnover_annualized: float
    capacity_score: float
    tail_loss_p99: float
    net_pnl_curve: list[float]
    gross_pnl_curve: list[float]
    total_return: float
    n_periods: int
    cost_drag: float


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class SignalEvaluator:
    """Evaluate a weight panel against future returns with a cost model.

    Pure logic; no nautilus_trader dependency.

    Parameters
    ----------
    periods_per_year:
        Annualisation factor.  Use ``252`` for daily (default), ``252 *
        6.5`` for hourly US equities, ``365 * 24`` for hourly crypto, etc.
    """

    def __init__(self, *, periods_per_year: int = 252) -> None:
        self.periods_per_year = periods_per_year

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        weight_panel: pl.DataFrame,
        future_returns: pl.DataFrame,
        cost_model: CostModel,
    ) -> SignalEvalResult:
        """Compute signal evaluation metrics.

        Parameters
        ----------
        weight_panel:
            Shape ``(T, N+1)``.  Column 0 must be named ``"ts"``; columns
            1..N are symbol weights (float, may contain NaN for "asset not
            in universe at this timestamp").
        future_returns:
            Shape ``(T, N+1)``.  Same layout as ``weight_panel``.  Column
            values are the *forward* returns for the corresponding
            ``ts`` row.  Symbol columns must match ``weight_panel`` (they
            can be a superset; an inner join on ``"ts"`` is performed and
            only common symbol columns are used).
        cost_model:
            :class:`~tinohelm.signal.types.CostModel` declaring per-side
            costs (fee + slippage - rebate).

        Returns
        -------
        SignalEvalResult
        """
        weights, returns, T = self._align(weight_panel, future_returns)

        # -- Zero-period edge case -----------------------------------------
        if T == 0:
            return SignalEvalResult(
                sharpe=0.0,
                mdd=0.0,
                turnover_annualized=0.0,
                capacity_score=0.0,
                tail_loss_p99=0.0,
                net_pnl_curve=[],
                gross_pnl_curve=[],
                total_return=0.0,
                n_periods=0,
                cost_drag=0.0,
            )

        # -- Drop trailing rows where every return is NaN ------------------
        # forward-return panels produced by close.shift(-1)/close-1 always
        # have a fully-NaN last row (no future bar exists).  nansum would
        # silently treat that row as 0 gross PnL while turnover/cost are
        # still charged → systematic bias in Sharpe, MDD, cost_drag, n_periods.
        valid_rows = np.where(np.isfinite(returns).any(axis=1))[0]
        if len(valid_rows) < T:
            last_valid = int(valid_rows[-1]) + 1 if len(valid_rows) > 0 else 0
            weights = weights[:last_valid]
            returns = returns[:last_valid]
            T = last_valid

        # Re-check after trimming.
        if T == 0:
            return SignalEvalResult(
                sharpe=0.0,
                mdd=0.0,
                turnover_annualized=0.0,
                capacity_score=0.0,
                tail_loss_p99=0.0,
                net_pnl_curve=[],
                gross_pnl_curve=[],
                total_return=0.0,
                n_periods=0,
                cost_drag=0.0,
            )

        # -- Gross period returns ------------------------------------------
        # nansum treats NaN weights as 0-contribution.
        gross = np.nansum(weights * returns, axis=1)  # (T,)

        # -- Single-sided turnover per period --------------------------------
        # Period 0: Σ|w[0]| (enter from flat).
        # Period t>0: 0.5 × Σ|w[t] - w[t-1]|.
        turnover_per_period = np.empty(T)
        turnover_per_period[0] = np.nansum(np.abs(weights[0]))
        if T > 1:
            delta = weights[1:] - weights[:-1]  # (T-1, N) — NaN propagates
            turnover_per_period[1:] = 0.5 * np.nansum(np.abs(delta), axis=1)

        # -- Cost -------------------------------------------------------------
        # total_per_side (bps) = fee + slippage - rebate
        cost_rate = (
            cost_model.fee_bps_per_side
            + cost_model.slippage_bps_per_side
            - cost_model.rebate_bps_per_side
        ) / 10_000.0
        costs = turnover_per_period * cost_rate  # (T,)

        # -- Net period returns -----------------------------------------------
        net = gross - costs  # (T,)

        # -- Sharpe (annualised) ---------------------------------------------
        if T > 1:
            net_std = float(np.std(net, ddof=1))
            if net_std > 1e-12:
                sharpe = (float(np.mean(net)) / net_std) * np.sqrt(self.periods_per_year)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # -- Cumulative curves -----------------------------------------------
        cum_net = np.cumsum(net)
        cum_gross = np.cumsum(gross)

        # -- MDD of net PnL --------------------------------------------------
        mdd = self._compute_mdd(cum_net)

        # -- Turnover annualised ---------------------------------------------
        turnover_annualized = float(np.mean(turnover_per_period)) * self.periods_per_year

        # -- Capacity score --------------------------------------------------
        capacity_per_period: list[float] = []
        for t in range(T):
            abs_w = np.abs(weights[t])
            valid = ~np.isnan(abs_w)
            if not np.any(valid):
                continue
            total = float(np.sum(abs_w[valid]))
            if total < 1e-12:
                # All-zero weights at this timestamp — skip (not concentrated).
                continue
            top1 = float(np.max(abs_w[valid]))
            capacity_per_period.append(1.0 - top1 / total)
        capacity_score = float(np.mean(capacity_per_period)) if capacity_per_period else 0.0

        # -- Tail loss (1st percentile of net returns) -----------------------
        tail_loss_p99 = float(np.percentile(net, 1))

        # -- Cost drag -------------------------------------------------------
        cost_drag = float(np.sum(costs))

        return SignalEvalResult(
            sharpe=float(sharpe),
            mdd=float(mdd),
            turnover_annualized=float(turnover_annualized),
            capacity_score=float(capacity_score),
            tail_loss_p99=float(tail_loss_p99),
            net_pnl_curve=cum_net.tolist(),
            gross_pnl_curve=cum_gross.tolist(),
            total_return=float(cum_net[-1]),
            n_periods=int(T),
            cost_drag=float(cost_drag),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _align(
        weight_panel: pl.DataFrame,
        future_returns: pl.DataFrame,
    ) -> tuple[np.ndarray, np.ndarray, int]:
        """Inner-join on ``"ts"`` and return aligned numpy arrays.

        Parameters
        ----------
        weight_panel, future_returns:
            Both must have a ``"ts"`` column.  Symbol columns are the
            intersection of both frames (excluding ``"ts"``).

        Returns
        -------
        tuple[np.ndarray, np.ndarray, int]
            ``(weights (T, N), returns (T, N), T)`` where ``T`` is the
            number of aligned rows and ``N`` is the number of common
            symbol columns.
        """
        # Determine common symbol columns (preserve weight_panel order).
        ret_sym_cols = {c for c in future_returns.columns if c != "ts"}
        sym_cols = [c for c in weight_panel.columns if c != "ts" and c in ret_sym_cols]

        if not sym_cols:
            # No common columns — return 0 periods.
            return np.empty((0, 0)), np.empty((0, 0)), 0

        # Inner-join on ts; suffix "_ret" applied to right-side duplicates.
        joined = weight_panel.join(
            future_returns.select(["ts", *sym_cols]),
            on="ts",
            how="inner",
            suffix="_ret",
        )

        T = len(joined)
        if T == 0:
            N = len(sym_cols)
            return np.empty((0, N)), np.empty((0, N)), 0

        weights_arr = joined.select(sym_cols).to_numpy().astype(np.float64, copy=True)
        ret_cols_in_joined = [f"{c}_ret" for c in sym_cols]
        returns_arr = joined.select(ret_cols_in_joined).to_numpy().astype(np.float64, copy=True)

        return weights_arr, returns_arr, T

    @staticmethod
    def _compute_mdd(pnl_curve: np.ndarray) -> float:
        """Max drawdown of a cumulative PnL curve.

        Parameters
        ----------
        pnl_curve:
            1-D array of cumulative PnL values.

        Returns
        -------
        float
            Non-negative max-drawdown value (e.g. ``0.15`` for 15%).
        """
        if len(pnl_curve) == 0:
            return 0.0
        running_max = np.maximum.accumulate(pnl_curve)
        drawdown = pnl_curve - running_max  # always ≤ 0
        return float(-np.min(drawdown))
