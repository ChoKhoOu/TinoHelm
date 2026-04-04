"""bookDepth CSV → BinanceBookDepth custom data converter.

CSV schema (HAS header row):
  timestamp, percentage, depth, notional

Each row represents the cumulative order book depth at a given
percentage distance from mid-price. Not a full order book snapshot
but an aggregated depth metric.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"timestamp", "percentage", "depth", "notional"}


@dataclass
class BinanceBookDepth:
    """Aggregated order book depth metric at a price percentage level."""
    symbol: str
    percentage: float
    depth: float
    notional: float
    ts_event: int
    ts_init: int


@register("bookDepth")
class BookDepthConverter:
    """Converts Binance Vision bookDepth CSV to BinanceBookDepth objects.

    Note: This CSV has a header row (unlike klines/trades).
    """

    supports_chunked = True

    def validate_schema(self, df: pd.DataFrame) -> None:
        if hasattr(df.columns[0], 'lower'):
            missing = _REQUIRED_COLUMNS - set(df.columns)
            if missing:
                raise SchemaError(
                    f"bookDepth CSV missing columns: {missing}. "
                    f"Got: {list(df.columns)}"
                )
        elif len(df.columns) < 4:
            raise SchemaError(
                f"bookDepth CSV requires at least 4 columns, "
                f"got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        symbol: str = kwargs.get("symbol", str(instrument.id))

        records: list[BinanceBookDepth] = []
        for row in df.itertuples(index=False):
            ts_str = str(row.timestamp) if hasattr(row, 'timestamp') else str(row[0])
            ts_ns = _parse_timestamp_ns(ts_str)
            pct = float(row.percentage if hasattr(row, 'percentage') else row[1])
            depth = float(row.depth if hasattr(row, 'depth') else row[2])
            notional = float(row.notional if hasattr(row, 'notional') else row[3])

            records.append(BinanceBookDepth(
                symbol=symbol,
                percentage=pct,
                depth=depth,
                notional=notional,
                ts_event=ts_ns,
                ts_init=ts_ns,
            ))

        logger.debug(
            "Converted %d bookDepth rows to %d objects",
            len(df), len(records),
        )
        return records

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)


def _parse_timestamp_ns(ts_str: str) -> int:
    """Parse timestamp string to nanoseconds.

    Handles both millisecond integers and datetime strings
    like '2025-01-01 00:00:07'.
    """
    try:
        ms = int(ts_str)
        return ms * 1_000_000
    except ValueError:
        dt = pd.Timestamp(ts_str, tz="UTC")
        return int(dt.timestamp() * 1_000_000_000)
