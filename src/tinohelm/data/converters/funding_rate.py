"""fundingRate CSV → BinanceFundingRate custom Data converter."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

# Binance Vision fundingRate CSV columns (no header row)
# calc_time, funding_interval_hours, last_funding_rate, ...
_COLUMN_NAMES = ["calc_time", "funding_interval_hours", "last_funding_rate"]


@dataclass
class BinanceFundingRate:
    """Custom data object for Binance funding rate records.

    Not a full NT Data subclass — used as a plain dataclass for storage.
    Pipeline writers handle serialization separately.
    """
    symbol: str
    funding_rate: float
    funding_time_ms: int
    ts_event: int
    ts_init: int


@register("fundingRate")
class FundingRateConverter:
    """fundingRate CSV (no header) → BinanceFundingRate."""

    supports_chunked = False

    def validate_schema(self, df: pd.DataFrame) -> None:
        if len(df.columns) < 3:
            raise SchemaError(
                f"fundingRate CSV requires at least 3 columns, got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        symbol: str = kwargs.get("symbol", str(instrument.id))

        # Assign column names if integer columns (no-header read)
        if len(df.columns) >= len(_COLUMN_NAMES):
            df = df.iloc[:, : len(_COLUMN_NAMES)].copy()
            df.columns = _COLUMN_NAMES
        elif isinstance(df.columns[0], int):
            cols = _COLUMN_NAMES[: len(df.columns)]
            df = df.copy()
            df.columns = cols

        records: list[BinanceFundingRate] = []
        for row in df.itertuples(index=False):
            funding_time_ms = int(row.calc_time)
            ts_ns = funding_time_ms * 1_000_000  # ms → ns
            records.append(BinanceFundingRate(
                symbol=symbol,
                funding_rate=float(row.last_funding_rate),
                funding_time_ms=funding_time_ms,
                ts_event=ts_ns,
                ts_init=ts_ns,
            ))

        logger.debug(
            "Converted %d fundingRate rows to %d BinanceFundingRate objects",
            len(df), len(records),
        )
        return records

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)
