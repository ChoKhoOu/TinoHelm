"""One-shot script to build the regression oracle parquet for s06.

Run once to (re)generate ``regression_oracle.parquet`` next to this file.
After that, ``test_builtins.py`` reads the parquet for value comparison —
this script is **not** invoked by tests.

Approach
--------
Compute ground-truth outputs for all 9 non-experimental built-in factors
using **pure numpy/pandas** (formulas mirrored verbatim from the legacy
pandas kernels).  This gives us a stable oracle that the polars
implementation must match within ``abs <= 1e-6``.

Layout (T = 20 bars × N = 5 symbols)
------------------------------------
- Inputs:  ``ts``, ``close_<sym>``, ``high_<sym>``, ``low_<sym>``,
  ``volume_<sym>``, ``funding_rate_<sym>``  (all 5 symbols)
- Outputs: 9 factor name columns × 5 symbols → 45 ``<factor>_<sym>`` cols

Each row is one timestamp; each cell is the legacy-pandas value.
NaN cells are written as null and round-trip through parquet.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd


T = 40
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]
N = len(SYMBOLS)
RNG = np.random.default_rng(20260426)


def _build_inputs() -> dict[str, pd.DataFrame]:
    """Build deterministic input panels (DatetimeIndex, columns=SYMBOLS)."""
    idx = pd.date_range(
        dt.datetime(2024, 1, 1), periods=T, freq="1h", name="ts"
    )

    # Random walks per symbol so cross-section + time-series stats are non-trivial.
    log_ret = RNG.normal(0.0, 0.01, size=(T, N))
    close = pd.DataFrame(
        100.0 * np.exp(np.cumsum(log_ret, axis=0)), index=idx, columns=SYMBOLS
    )
    # HIGH = close * (1 + uniform[0, 0.02]); LOW = close * (1 - uniform[0, 0.02])
    high_jitter = RNG.uniform(0.0, 0.02, size=(T, N))
    low_jitter = RNG.uniform(0.0, 0.02, size=(T, N))
    high = close * (1.0 + high_jitter)
    low = close * (1.0 - low_jitter)

    # Strictly positive volume.
    volume = pd.DataFrame(
        RNG.lognormal(10.0, 0.5, size=(T, N)), index=idx, columns=SYMBOLS
    )

    funding = pd.DataFrame(
        RNG.normal(0.0, 0.001, size=(T, N)), index=idx, columns=SYMBOLS
    )

    return {
        "close": close,
        "high": high,
        "low": low,
        "volume": volume,
        "funding_rate": funding,
    }


# ---------------------------------------------------------------------------
# Legacy pandas kernels (verbatim from src/tinohelm/factor/builtins/*.py)
# ---------------------------------------------------------------------------


def _ret_N(close: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    return close.pct_change(n)


def _rsi_signal(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / (loss + 1e-12)
    rsi = 100 - 100 / (1 + rs)
    return rsi - 50


def _parkinson_vol(
    high: pd.DataFrame, low: pd.DataFrame, n: int = 20
) -> pd.DataFrame:
    log_hl = np.log(high / (low + 1e-12))
    return np.sqrt((log_hl ** 2).rolling(n).mean() / (4 * np.log(2)))


def _vol_ratio(close: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.DataFrame:
    ret = close.pct_change()
    vol_f = ret.rolling(fast).std()
    vol_s = ret.rolling(slow).std()
    return vol_f / (vol_s + 1e-12)


def _obv_slope(
    close: pd.DataFrame, volume: pd.DataFrame, n: int = 20
) -> pd.DataFrame:
    direction = np.sign(close.diff())
    obv = (direction * volume).cumsum()
    return obv.diff(n) / n


def _vwap_dev(
    high: pd.DataFrame,
    low: pd.DataFrame,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    n: int = 20,
) -> pd.DataFrame:
    tp = (high + low + close) / 3
    vwap = (tp * volume).rolling(n).sum() / (volume.rolling(n).sum() + 1e-12)
    return (close - vwap) / (vwap + 1e-12)


def _amihud_illiq(
    close: pd.DataFrame, volume: pd.DataFrame, n: int = 20
) -> pd.DataFrame:
    ret = close.pct_change().abs()
    dollar_vol = close * volume
    return (ret / (dollar_vol + 1e-12)).rolling(n).mean()


def _funding_rate_level(funding_rate: pd.DataFrame) -> pd.DataFrame:
    return funding_rate.copy()


def _funding_rate_mom(funding_rate: pd.DataFrame, n: int = 1) -> pd.DataFrame:
    return funding_rate.diff(n)


# ---------------------------------------------------------------------------
# Build oracle table (long-format friendly: every output column named
# "<factor>__<symbol>").
# ---------------------------------------------------------------------------


def main() -> None:
    inputs = _build_inputs()
    close, high, low, volume, funding_rate = (
        inputs["close"],
        inputs["high"],
        inputs["low"],
        inputs["volume"],
        inputs["funding_rate"],
    )

    out: dict[str, pd.Series] = {}

    # 1. Inputs (5 fields × 5 symbols = 25 cols).
    for fname, df in inputs.items():
        for sym in SYMBOLS:
            out[f"input_{fname}__{sym}"] = df[sym].rename(f"input_{fname}__{sym}")

    # 2. Factor outputs (9 factors × 5 symbols = 45 cols).
    factor_outputs: dict[str, pd.DataFrame] = {
        # Use lookback=20 for 7 of them, 14 for rsi_signal, fast/slow=5/20 for vol_ratio.
        "ret_N": _ret_N(close, n=20),
        "rsi_signal": _rsi_signal(close, n=14),
        "parkinson_vol": _parkinson_vol(high, low, n=20),
        "vol_ratio": _vol_ratio(close, fast=5, slow=20),
        "obv_slope": _obv_slope(close, volume, n=20),
        "vwap_dev": _vwap_dev(high, low, close, volume, n=20),
        "amihud_illiq": _amihud_illiq(close, volume, n=20),
        "funding_rate_level": _funding_rate_level(funding_rate),
        "funding_rate_mom": _funding_rate_mom(funding_rate, n=1),
    }
    for fname, df in factor_outputs.items():
        for sym in SYMBOLS:
            out[f"factor_{fname}__{sym}"] = df[sym].rename(f"factor_{fname}__{sym}")

    # Stitch into a single wide DataFrame indexed by ts.
    oracle = pd.concat(out, axis=1)
    oracle.index.name = "ts"
    oracle = oracle.reset_index()

    # Persist as parquet.
    out_path = Path(__file__).parent / "regression_oracle.parquet"
    oracle.to_parquet(out_path, index=False)
    print(f"Wrote oracle: {out_path} ({len(oracle)} rows × {len(oracle.columns)} cols)")


if __name__ == "__main__":
    main()
