"""markPriceKlines CSV → MarkPriceUpdate converter."""
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
    """Converts Binance Vision markPriceKlines CSV to NT MarkPriceUpdate."""

    supports_chunked = False

    def validate_schema(self, df: pd.DataFrame) -> None:
        if len(df.columns) < 6:
            raise SchemaError(
                f"markPriceKlines CSV requires at least 6 columns, got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        from nautilus_trader.model.data import MarkPriceUpdate

        if len(df.columns) >= len(_COLUMN_NAMES):
            df = df.iloc[:, : len(_COLUMN_NAMES)].copy()
            df.columns = _COLUMN_NAMES
        elif isinstance(df.columns[0], int):
            cols = _COLUMN_NAMES[: len(df.columns)]
            df = df.copy()
            df.columns = cols

        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce")
        df = df[["close", "close_time"]].dropna().sort_values("close_time")
        df = df.drop_duplicates(subset=["close_time"], keep="last")

        if df.empty:
            logger.warning("markPriceKlines DataFrame empty after processing")
            return []

        records = [
            MarkPriceUpdate(
                instrument_id=instrument.id,
                value=instrument.make_price(float(row.close)),
                ts_event=int(row.close_time) * 1_000_000,
                ts_init=int(row.close_time) * 1_000_000,
            )
            for row in df.itertuples(index=False)
        ]
        logger.debug(
            "Converted %d markPriceKlines rows to %d MarkPriceUpdate objects", len(df), len(records)
        )
        return records

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)
