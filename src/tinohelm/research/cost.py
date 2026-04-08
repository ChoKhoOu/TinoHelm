"""Cost analysis — edge waterfall and fee drag estimation."""
from __future__ import annotations


def edge_waterfall(
    ic_mean: float,
    turnover_daily: float,
    fee_rate: float = 0.0004,
    slippage_bps: float = 1.0,
) -> dict:
    """Compute gross -> net edge waterfall.

    Approximate: gross edge ~= IC * volatility * sqrt(252) in bps
    For simplicity, use IC * 10000 as gross edge proxy in bps/trade.
    """
    # Gross edge: IC-based approximation (bps per trade)
    gross_bps = abs(ic_mean) * 10000  # rough proxy

    # Fee cost per trade (both sides)
    fee_bps = fee_rate * 2 * 10000  # convert to bps

    # Scale by daily turnover (how many trades per day)
    daily_fee_bps = fee_bps * turnover_daily
    daily_slippage_bps = slippage_bps * turnover_daily

    # Daily gross edge
    daily_gross_bps = gross_bps * turnover_daily

    net_bps = daily_gross_bps - daily_fee_bps - daily_slippage_bps

    return {
        "gross_edge_bps": round(daily_gross_bps, 2),
        "fee_cost_bps": round(daily_fee_bps, 2),
        "slippage_bps": round(daily_slippage_bps, 2),
        "net_edge_bps": round(net_bps, 2),
    }
