"""markPriceKlines CSV → Bar (PriceType.MID, volume=0) converter."""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

# Binance Vision markPriceKlines CSV columns (no header row, 6 columns)
_COLUMN_NAMES = ["open_time", "open", "high", "low", "close", "close_time"]


@register("markPriceKlines")
class MarkPriceConverter:
    """Converts Binance Vision markPriceKlines CSV (no header) to NT Bar objects.

    Volume is set to 0 since mark price bars carry no trading volume.
    Uses PriceType.MID.
    """

    supports_chunked = False

    def validate_schema(self, df: pd.DataFrame) -> None:
        if len(df.columns) < 6:
            raise SchemaError(
                f"markPriceKlines CSV requires at least 6 columns, got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        bar_type = kwargs.get("bar_type")
        if bar_type is None:
            raise ValueError("markPriceKlines converter requires 'bar_type' kwarg")

        from nautilus_trader.persistence.wranglers import BarDataWrangler

        # Assign column names if integer columns (no-header read)
        if len(df.columns) >= len(_COLUMN_NAMES):
            df = df.iloc[:, : len(_COLUMN_NAMES)].copy()
            df.columns = _COLUMN_NAMES
        elif isinstance(df.columns[0], int):
            cols = _COLUMN_NAMES[: len(df.columns)]
            df = df.copy()
            df.columns = cols

        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = 0.0
        df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.index.name = "timestamp"
        df = df[["open", "high", "low", "close", "volume"]].sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna(subset=["open", "high", "low", "close"])

        if df.empty:
            logger.warning("markPriceKlines DataFrame empty after processing")
            return []

        wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)
        bars = wrangler.process(df)
        logger.debug(
            "Converted %d markPriceKlines rows to %d Bar objects", len(df), len(bars)
        )
        return bars

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)
