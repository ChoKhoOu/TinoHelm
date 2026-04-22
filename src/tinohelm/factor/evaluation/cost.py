"""Cost analysis — edge waterfall and fee drag estimation.

Migrated from ``research.cost.edge_waterfall`` with identical math (AC-13.2).
"""
from __future__ import annotations


def edge_waterfall(
    ic_mean: float,
    turnover_daily: float,
    fee_rate: float = 0.0004,
    slippage_bps: float = 1.0,
) -> dict:
    """Compute gross → net edge waterfall.

    Approximate: gross edge ≈ ``|IC| * 10000`` bps per trade (simplified
    proxy — the legacy implementation uses this same approximation).

    Parameters
    ----------
    ic_mean : float
        Mean information coefficient (sign ignored — ``abs(ic_mean)`` is used).
    turnover_daily : float
        Fraction of positions that change per day (output of ``compute_turnover``).
    fee_rate : float, default 0.0004
        Round-trip fee rate per side (0.04 % is the Binance perp taker default).
    slippage_bps : float, default 1.0
        Per-trade slippage cost in basis points.

    Returns
    -------
    dict
        ``{"gross_edge_bps", "fee_cost_bps", "slippage_bps", "net_edge_bps"}`` —
        all values rounded to 2 decimals to match legacy output.
    """
    gross_bps = abs(ic_mean) * 10000

    fee_bps = fee_rate * 2 * 10000
    daily_fee_bps = fee_bps * turnover_daily
    daily_slippage_bps = slippage_bps * turnover_daily
    daily_gross_bps = gross_bps * turnover_daily

    net_bps = daily_gross_bps - daily_fee_bps - daily_slippage_bps

    return {
        "gross_edge_bps": round(daily_gross_bps, 2),
        "fee_cost_bps": round(daily_fee_bps, 2),
        "slippage_bps": round(daily_slippage_bps, 2),
        "net_edge_bps": round(net_bps, 2),
    }


__all__ = ["edge_waterfall"]
