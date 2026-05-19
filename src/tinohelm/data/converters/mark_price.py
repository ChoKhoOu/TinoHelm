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
        df = df[["close", "close_time"]].dropna().sort_values(by="close_time")

        conflicts = df.groupby("close_time")["close"].nunique(dropna=True)
        conflicting_times = conflicts[conflicts > 1]
        if not conflicting_times.empty:
            first_conflict = conflicting_times.index[0]
            raise ValueError(f"Conflicting mark price rows for close_time={int(first_conflict)}")

        df = df.drop_duplicates(subset=["close_time"], keep="last")

        if df.empty:
            logger.warning("markPriceKlines DataFrame empty after processing")
            return []

        records = [
            MarkPriceUpdate(
                instrument_id=instrument.id,
                value=instrument.make_price(float(close)),
                ts_event=int(close_time) * 1_000_000,
                ts_init=int(close_time) * 1_000_000,
            )
            for close, close_time in df.itertuples(index=False, name=None)
        ]
        logger.debug(
            "Converted %d markPriceKlines rows to %d MarkPriceUpdate objects", len(df), len(records)
        )
        return records

