"""Leaf math primitives shared by backtest helpers.

This module is deliberately dependency-free (standard library only) so it
can be imported from modules that need to stay out of the NautilusTrader
and ``pandas`` import chain that :mod:`tinohelm.backtest.result` pulls
in via its package ``__init__``.

Both :mod:`tinohelm.backtest.result.statistics` and
:mod:`tinohelm.backtest.optimizer_helpers` import from here, so there is
still a single source of truth for the normal distribution approximations.
"""
from __future__ import annotations

import math


def norm_ppf(p: float) -> float:
    """Approximate inverse normal CDF (percent-point function).

    Uses the rational approximation from Abramowitz & Stegun, formula
    26.2.23.  Accurate to ~4.5e-4 for ``0 < p < 1``.  Values at or
    outside the ``(0, 1)`` interval return ``0.0`` (matching the legacy
    behaviour that ``tinohelm.backtest.result.statistics`` has relied on
    since before the package was split).
    """
    if p <= 0 or p >= 1:
        return 0.0
    if p < 0.5:
        return -norm_ppf(1 - p)
    t = (-2.0 * math.log(1.0 - p)) ** 0.5
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (
        1.0 + d1 * t + d2 * t * t + d3 * t * t * t
    )


def norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (no scipy dependency)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
