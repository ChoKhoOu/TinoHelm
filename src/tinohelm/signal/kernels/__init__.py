"""Built-in signal kernel templates.

The 5 built-in kernels provide ready-to-use signal generation logic that
takes a factor panel and emits a portfolio weight panel.  Each kernel is
a *pure* function (no state) and shares a consistent signature::

    kernel(
        factor_panel: pl.DataFrame,    # column 0 = "ts", columns 1..N = symbols
        params: dict[str, Any],         # method-specific parameters
        constraints: dict[str, float],  # gross_exposure / net_exposure / max_position
    ) -> pl.DataFrame                   # same layout: "ts" + N weight columns

The 5 kernels:

* :func:`top_k_long_short` — equal-weight long top-K and short bottom-K.
* :func:`quantile_long_short` — long-top-quantile, short-bottom-quantile.
* :func:`threshold_signed` — fixed weights when factor crosses thresholds.
* :func:`zscore_clip` — cross-section z-score, clipped to ``±clip``.
* :func:`rank_to_weight` — rank-percentile mapped to weight via a power.

All kernels delegate the final position-constraint enforcement to
:func:`tinohelm.signal.kernel.normalize_to_constraints` so behaviour
matches the SignalSpec contract uniformly.
"""
from tinohelm.signal.kernels.quantile_long_short import quantile_long_short
from tinohelm.signal.kernels.rank_to_weight import rank_to_weight
from tinohelm.signal.kernels.threshold_signed import threshold_signed
from tinohelm.signal.kernels.top_k_long_short import top_k_long_short
from tinohelm.signal.kernels.zscore_clip import zscore_clip

__all__ = [
    "quantile_long_short",
    "rank_to_weight",
    "threshold_signed",
    "top_k_long_short",
    "zscore_clip",
]
