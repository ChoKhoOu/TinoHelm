"""premiumIndexKlines CSV → Bar (PriceType.LAST) converter."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

# Binance Vision premiumIndexKlines CSV columns (no header row, same as klines)
_COLUMN_NAMES = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


@register("premiumIndexKlines")
class PremiumIndexConverter:
    """Converts Binance Vision premiumIndexKlines CSV (no header) to NT Bar objects.

    Same schema as klines (12 columns). Uses PriceType.LAST.
    """

    supports_chunked = False

    def validate_schema(self, df: pd.DataFrame) -> None:
        if len(df.columns) < 7:
            raise SchemaError(
                f"premiumIndexKlines CSV requires at least 7 columns, got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        bar_type = kwargs.get("bar_type")
        if bar_type is None:
            raise ValueError("premiumIndexKlines converter requires 'bar_type' kwarg")

        from nautilus_trader.persistence.wranglers import BarDataWrangler

        # Assign column names if integer columns (no-header read)
        if len(df.columns) >= len(_COLUMN_NAMES):
            df = df.iloc[:, : len(_COLUMN_NAMES)].copy()
            df.columns = _COLUMN_NAMES
        elif isinstance(df.columns[0], int):
            cols = _COLUMN_NAMES[: len(df.columns)]
            df = df.copy()
            df.columns = cols

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.index.name = "timestamp"
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna()

        if df.empty:
            logger.warning("premiumIndexKlines DataFrame empty after processing")
            return []

        wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
        bars = wrangler.process(df)
        logger.debug(
            "Converted %d premiumIndexKlines rows to %d Bar objects", len(df), len(bars)
        )
        return bars

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)
