"""Load data from catalog into pandas DataFrames for research.

Supports multiple data types:
- bar:          OHLCV from Parquet (NT ParquetDataCatalog)
- trade_tick:   TradeTick from Parquet (NT ParquetDataCatalog)
- funding_rate: Funding rate from JSON cache (~/.tino/data/funding_rates/)
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_CATALOG = Path.home() / ".tino" / "data" / "catalog"

# ── Bar type helpers ──────────────────────────────────────────────

_UNIT_MAP = {
    "1m": "MINUTE", "3m": "MINUTE", "5m": "MINUTE", "15m": "MINUTE", "30m": "MINUTE",
    "1h": "HOUR", "2h": "HOUR", "4h": "HOUR", "6h": "HOUR", "8h": "HOUR", "12h": "HOUR",
    "1d": "DAY",
}
_MULT_MAP = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 1, "2h": 2, "4h": 4, "6h": 6, "8h": 8, "12h": 12,
    "1d": 1,
}


def _bar_type_dir(symbol: str, interval: str) -> str:
    """Build NT bar type directory name."""
    venue = "BINANCE"
    unit = _UNIT_MAP.get(interval, "MINUTE")
    mult = _MULT_MAP.get(interval, 1)
    return f"{symbol}.{venue}-{mult}-{unit}-LAST-EXTERNAL"


def _instrument_id(symbol: str) -> str:
    """Build NT instrument ID string."""
    s = symbol if "." in symbol else f"{symbol}.BINANCE"
    return s


# ── Type mapping ──────────────────────────────────────────────────
# Accepts both Vision data type names and internal category names.

_BAR_TYPES = frozenset({
    "bar", "klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines",
})
_TICK_TYPES = frozenset({"trade_tick", "aggTrades", "trades"})
_FUNDING_TYPES = frozenset({"funding_rate", "fundingRate"})


# ── Unified dispatcher ────────────────────────────────────────────

def load_data(
    symbol: str,
    data_type: str,
    interval: str | None = None,
    start: str | None = None,
    end: str | None = None,
    catalog_path: str | Path | None = None,
) -> pd.DataFrame:
    """Unified data loader — dispatches by data_type.

    Parameters
    ----------
    symbol : str
        TinoHelm-style symbol, e.g. "BTCUSDT-PERP"
    data_type : str
        Vision type: "klines", "markPriceKlines", "indexPriceKlines",
        "premiumIndexKlines", "aggTrades", "trades", "fundingRate"
        Or category: "bar", "trade_tick", "funding_rate"
    interval : str, optional
        Required for bar data (e.g. "1m", "5m")
    start / end : str, optional
        Date filter (ISO format)
    catalog_path : str or Path, optional
        Override default catalog path

    Returns
    -------
    pd.DataFrame with DatetimeIndex
    """
    if data_type in _BAR_TYPES:
        return load_bars(symbol, interval or "1m", start, end, catalog_path)
    elif data_type in _TICK_TYPES:
        return load_trade_ticks(symbol, start, end, catalog_path)
    elif data_type in _FUNDING_TYPES:
        return load_funding_rates(symbol, start, end)
    else:
        raise ValueError(
            f"Unsupported data_type: {data_type!r}. "
            f"Expected one of: {sorted(_BAR_TYPES | _TICK_TYPES | _FUNDING_TYPES)}"
        )


# ── Bar loader ────────────────────────────────────────────────────

def load_bars(
    symbol: str,
    interval: str = "1m",
    start: str | None = None,
    end: str | None = None,
    catalog_path: str | Path | None = None,
) -> pd.DataFrame:
    """Load bar data from Parquet catalog.

    Returns DataFrame with columns: open, high, low, close, volume
    Index: DatetimeIndex named 'timestamp'
    """
    catalog = Path(catalog_path) if catalog_path else DEFAULT_CATALOG
    bar_dir = catalog / "data" / "bar" / _bar_type_dir(symbol, interval)

    if not bar_dir.exists():
        raise FileNotFoundError(f"No bar data at {bar_dir}")

    df = pd.read_parquet(bar_dir)
    logger.info("Loaded %d bars from %s", len(df), bar_dir)

    # NT Parquet schema: ts_init is the bar closing time in nanoseconds
    if "ts_init" in df.columns:
        df["timestamp"] = pd.to_datetime(df["ts_init"], unit="ns")
        df = df.set_index("timestamp").sort_index()

    # Rename NT columns to standard names
    col_map = {}
    for col in df.columns:
        lower = col.lower()
        if "open" in lower:
            col_map[col] = "open"
        elif "high" in lower:
            col_map[col] = "high"
        elif "low" in lower:
            col_map[col] = "low"
        elif "close" in lower and "close" not in col_map.values():
            col_map[col] = "close"
        elif "volume" in lower and "volume" not in col_map.values():
            col_map[col] = "volume"
    if col_map:
        df = df.rename(columns=col_map)

    # Ensure numeric
    for c in ["open", "high", "low", "close", "volume"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Filter date range
    if start:
        df = df[df.index >= pd.Timestamp(start)]
    if end:
        df = df[df.index <= pd.Timestamp(end)]

    # Keep only OHLCV
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep]


# ── Trade tick loader ─────────────────────────────────────────────

def load_trade_ticks(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    catalog_path: str | Path | None = None,
) -> pd.DataFrame:
    """Load trade tick data from Parquet catalog.

    Returns DataFrame with columns: price, quantity, side
    - price:    float, trade price
    - quantity: float, trade size
    - side:     int, +1 = taker buy, -1 = taker sell
    Index: DatetimeIndex named 'timestamp'
    """
    catalog = Path(catalog_path) if catalog_path else DEFAULT_CATALOG
    inst_id = _instrument_id(symbol)
    tick_dir = catalog / "data" / "trade_tick" / inst_id

    if not tick_dir.exists():
        raise FileNotFoundError(f"No trade tick data at {tick_dir}")

    df = pd.read_parquet(tick_dir)
    logger.info("Loaded %d trade ticks from %s", len(df), tick_dir)

    # NT TradeTick Parquet columns: price, size, aggressor_side, trade_id, ts_event, ts_init
    if "ts_event" in df.columns:
        df["timestamp"] = pd.to_datetime(df["ts_event"], unit="ns")
        df = df.set_index("timestamp").sort_index()

    # Normalize columns
    result = pd.DataFrame(index=df.index)
    result["price"] = pd.to_numeric(df["price"], errors="coerce")
    result["quantity"] = pd.to_numeric(df["size"], errors="coerce")

    # aggressor_side: NT stores it as either the AggressorSide enum int (1=BUYER, 2=SELLER)
    # or the stringified form ("BUYER"/"SELLER"). The previous `side.dtype == object` check
    # silently broke under pandas 3, which loads string columns as `str` dtype rather than
    # `object`. Use a unified mapping dict — Series.map only matches keys with a compatible
    # type, so the irrelevant keys are no-ops in each case.
    if "aggressor_side" in df.columns:
        side = df["aggressor_side"]
        side_map = {"BUYER": 1, "SELLER": -1, 1: 1, 2: -1}
        result["side"] = side.map(side_map).fillna(0).astype(int)
    else:
        result["side"] = 0

    # Filter date range
    if start:
        result = result[result.index >= pd.Timestamp(start)]
    if end:
        result = result[result.index <= pd.Timestamp(end)]

    return result


# ── Funding rate loader ───────────────────────────────────────────

def load_funding_rates(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Load funding rate data from JSON cache.

    Returns DataFrame with columns: funding_rate, mark_price
    Index: DatetimeIndex named 'timestamp'
    """
    import json

    cache_dir = Path.home() / ".tino" / "data" / "funding_rates"
    cache_file = cache_dir / f"{symbol.lower()}.json"

    if not cache_file.exists():
        raise FileNotFoundError(f"No funding rate data at {cache_file}")

    with open(cache_file) as f:
        records = json.load(f)

    if not records:
        raise FileNotFoundError(f"Funding rate cache empty for {symbol}")

    df = pd.DataFrame(records)
    logger.info("Loaded %d funding rate records for %s", len(df), symbol)

    # JSON schema: funding_time_ms, funding_rate, mark_price
    if "funding_time_ms" in df.columns:
        df["timestamp"] = pd.to_datetime(df["funding_time_ms"], unit="ms")
        df = df.set_index("timestamp").sort_index()

    result = pd.DataFrame(index=df.index)
    result["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce")
    if "mark_price" in df.columns:
        result["mark_price"] = pd.to_numeric(df["mark_price"], errors="coerce")

    # Filter date range
    if start:
        result = result[result.index >= pd.Timestamp(start)]
    if end:
        result = result[result.index <= pd.Timestamp(end)]

    return result


# ── Availability check ────────────────────────────────────────────

def check_availability(
    symbol: str,
    data_type: str = "bar",
    interval: str = "1m",
    catalog_path: str | Path | None = None,
) -> dict:
    """Check if data is available and return info."""
    catalog = Path(catalog_path) if catalog_path else DEFAULT_CATALOG

    if data_type == "bar":
        data_dir = catalog / "data" / "bar" / _bar_type_dir(symbol, interval)
    elif data_type == "trade_tick":
        data_dir = catalog / "data" / "trade_tick" / _instrument_id(symbol)
    elif data_type == "funding_rate":
        cache_file = Path.home() / ".tino" / "data" / "funding_rates" / f"{symbol.lower()}.json"
        if not cache_file.exists():
            return {"available": False, "count": 0}
        try:
            import json
            with open(cache_file) as f:
                records = json.load(f)
            if not records:
                return {"available": False, "count": 0}
            ts = [r["funding_time_ms"] for r in records]
            return {
                "available": True,
                "count": len(records),
                "start": pd.Timestamp(min(ts), unit="ms").isoformat(),
                "end": pd.Timestamp(max(ts), unit="ms").isoformat(),
            }
        except Exception:
            return {"available": False, "count": 0}
    else:
        return {"available": False, "count": 0}

    if not data_dir.exists():
        return {"available": False, "count": 0}

    try:
        df = pd.read_parquet(data_dir, columns=["ts_init"])
        ts = pd.to_datetime(df["ts_init"], unit="ns")
        return {
            "available": True,
            "count": len(df),
            "start": ts.min().isoformat(),
            "end": ts.max().isoformat(),
        }
    except Exception:
        return {"available": False, "count": 0}
