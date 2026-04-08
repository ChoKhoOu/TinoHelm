"""Built-in factor library — vectorized computation on OHLCV data."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


# ── Factor metadata registry ──────────────────────────────────────────

FactorMeta = dict[str, Any]

BUILTIN_FACTORS: dict[str, FactorMeta] = {
    "ret_N": {
        "label": "N 周期收益率",
        "category": "动量",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 1, "max": 500, "label": "回看周期"}},
    },
    "mom_ratio": {
        "label": "均线比",
        "category": "动量",
        "data_type": "bar",
        "params": {
            "fast": {"default": 5, "min": 1, "max": 200, "label": "快线周期"},
            "slow": {"default": 20, "min": 5, "max": 500, "label": "慢线周期"},
        },
    },
    "roc": {
        "label": "变化率",
        "category": "动量",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 1, "max": 500, "label": "回看周期"}},
    },
    "rsi_signal": {
        "label": "RSI 信号",
        "category": "动量",
        "data_type": "bar",
        "params": {"lookback": {"default": 14, "min": 2, "max": 200, "label": "回看周期"}},
    },
    "realized_vol": {
        "label": "已实现波动率",
        "category": "波动",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 5, "max": 500, "label": "回看周期"}},
    },
    "vol_ratio": {
        "label": "波动率比",
        "category": "波动",
        "data_type": "bar",
        "params": {
            "fast": {"default": 5, "min": 1, "max": 200, "label": "快窗口"},
            "slow": {"default": 20, "min": 5, "max": 500, "label": "慢窗口"},
        },
    },
    "atr_norm": {
        "label": "归一化 ATR",
        "category": "波动",
        "data_type": "bar",
        "params": {"lookback": {"default": 14, "min": 2, "max": 200, "label": "回看周期"}},
    },
    "parkinson_vol": {
        "label": "Parkinson 波动率",
        "category": "波动",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 5, "max": 500, "label": "回看周期"}},
    },
    "vwap_dev": {
        "label": "VWAP 偏离",
        "category": "量价",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 5, "max": 500, "label": "回看周期"}},
    },
    "volume_surge": {
        "label": "成交量突增",
        "category": "量价",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 5, "max": 500, "label": "回看周期"}},
    },
    "obv_slope": {
        "label": "OBV 斜率",
        "category": "量价",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 5, "max": 200, "label": "回看周期"}},
    },
    "trade_imbalance": {
        "label": "买卖不平衡",
        "category": "微观结构",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 5, "max": 200, "label": "回看窗口"}},
    },
    "kyle_lambda": {
        "label": "价格冲击",
        "category": "微观结构",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 10, "max": 200, "label": "回看窗口"}},
    },
    "amihud_illiq": {
        "label": "Amihud 非流动性",
        "category": "微观结构",
        "data_type": "bar",
        "params": {"lookback": {"default": 20, "min": 5, "max": 200, "label": "回看周期"}},
    },
}


# ── Factor compute functions ──────────────────────────────────────────

def ret_N(df: pd.DataFrame, params: dict) -> pd.Series:
    """N-period return."""
    n = params.get("lookback", 20)
    return df["close"].pct_change(n)


def mom_ratio(df: pd.DataFrame, params: dict) -> pd.Series:
    """Fast/slow moving average ratio - 1."""
    fast = params.get("fast", 5)
    slow = params.get("slow", 20)
    sma_f = df["close"].rolling(fast).mean()
    sma_s = df["close"].rolling(slow).mean()
    return (sma_f / sma_s) - 1


def roc(df: pd.DataFrame, params: dict) -> pd.Series:
    """Rate of change."""
    n = params.get("lookback", 20)
    return df["close"] / df["close"].shift(n) - 1


def rsi_signal(df: pd.DataFrame, params: dict) -> pd.Series:
    """RSI centered around 0 (RSI - 50)."""
    n = params.get("lookback", 14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / (loss + 1e-12)
    rsi = 100 - 100 / (1 + rs)
    return rsi - 50  # center around 0


def realized_vol(df: pd.DataFrame, params: dict) -> pd.Series:
    """Realized volatility (rolling std of returns)."""
    n = params.get("lookback", 20)
    ret = df["close"].pct_change()
    return ret.rolling(n).std()


def vol_ratio(df: pd.DataFrame, params: dict) -> pd.Series:
    """Short-term / long-term volatility ratio."""
    fast = params.get("fast", 5)
    slow = params.get("slow", 20)
    ret = df["close"].pct_change()
    vol_f = ret.rolling(fast).std()
    vol_s = ret.rolling(slow).std()
    return vol_f / (vol_s + 1e-12)


def atr_norm(df: pd.DataFrame, params: dict) -> pd.Series:
    """Normalized ATR (ATR / close)."""
    n = params.get("lookback", 14)
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    return atr / (df["close"] + 1e-12)


def parkinson_vol(df: pd.DataFrame, params: dict) -> pd.Series:
    """Parkinson volatility estimator using high/low range."""
    n = params.get("lookback", 20)
    log_hl = np.log(df["high"] / (df["low"] + 1e-12))
    return np.sqrt((log_hl ** 2).rolling(n).mean() / (4 * np.log(2)))


def vwap_dev(df: pd.DataFrame, params: dict) -> pd.Series:
    """Price deviation from VWAP."""
    n = params.get("lookback", 20)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (tp * df["volume"]).rolling(n).sum() / (df["volume"].rolling(n).sum() + 1e-12)
    return (df["close"] - vwap) / (vwap + 1e-12)


def volume_surge(df: pd.DataFrame, params: dict) -> pd.Series:
    """Volume / rolling average volume."""
    n = params.get("lookback", 20)
    return df["volume"] / (df["volume"].rolling(n).mean() + 1e-12)


def obv_slope(df: pd.DataFrame, params: dict) -> pd.Series:
    """OBV slope over N periods."""
    n = params.get("lookback", 20)
    direction = np.sign(df["close"].diff())
    obv = (direction * df["volume"]).cumsum()
    return obv.diff(n) / n


def trade_imbalance(df: pd.DataFrame, params: dict) -> pd.Series:
    """Approximate buy/sell imbalance from bar data.
    Uses close position within high-low range as proxy.
    """
    n = params.get("lookback", 20)
    hl_range = df["high"] - df["low"]
    # Close-Low / High-Low as buy ratio proxy
    buy_ratio = (df["close"] - df["low"]) / (hl_range + 1e-12)
    sell_ratio = 1 - buy_ratio
    imbalance = (buy_ratio - sell_ratio) * df["volume"]
    return imbalance.rolling(n).mean() / (df["volume"].rolling(n).mean() + 1e-12)


def kyle_lambda(df: pd.DataFrame, params: dict) -> pd.Series:
    """Price impact proxy: |return| / volume."""
    n = params.get("lookback", 20)
    ret = df["close"].pct_change().abs()
    impact = ret / (df["volume"] + 1e-12)
    return impact.rolling(n).mean()


def amihud_illiq(df: pd.DataFrame, params: dict) -> pd.Series:
    """Amihud illiquidity: |return| / dollar_volume."""
    n = params.get("lookback", 20)
    ret = df["close"].pct_change().abs()
    dollar_vol = df["close"] * df["volume"]
    return (ret / (dollar_vol + 1e-12)).rolling(n).mean()


# ── Dispatch ──────────────────────────────────────────────────────────

_COMPUTE_MAP: dict[str, callable] = {
    "ret_N": ret_N,
    "mom_ratio": mom_ratio,
    "roc": roc,
    "rsi_signal": rsi_signal,
    "realized_vol": realized_vol,
    "vol_ratio": vol_ratio,
    "atr_norm": atr_norm,
    "parkinson_vol": parkinson_vol,
    "vwap_dev": vwap_dev,
    "volume_surge": volume_surge,
    "obv_slope": obv_slope,
    "trade_imbalance": trade_imbalance,
    "kyle_lambda": kyle_lambda,
    "amihud_illiq": amihud_illiq,
}


def compute_factor(name: str, df: pd.DataFrame, params: dict | None = None) -> pd.Series:
    """Compute a named factor with given parameters."""
    fn = _COMPUTE_MAP.get(name)
    if fn is None:
        raise ValueError(f"Unknown factor: {name}")
    meta = BUILTIN_FACTORS.get(name, {})
    # Fill defaults from meta
    full_params: dict[str, Any] = {}
    for pname, pdef in meta.get("params", {}).items():
        full_params[pname] = pdef.get("default", 0)
    if params:
        full_params.update(params)
    return fn(df, full_params)


def list_factors() -> dict[str, FactorMeta]:
    """Return all built-in factor metadata."""
    return dict(BUILTIN_FACTORS)
