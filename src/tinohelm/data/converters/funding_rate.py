"""fundingRate CSV → FundingRateUpdate converter."""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)


class BinanceFundingRate:
    def __init__(
        self,
        symbol: str,
        funding_rate: float,
        funding_time_ms: int,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.symbol = symbol
        self.funding_rate = funding_rate
        self.funding_time_ms = funding_time_ms
        self.ts_event = ts_event
        self.ts_init = ts_init


# Binance Vision fundingRate CSV columns (no header row)
# calc_time, funding_interval_hours, last_funding_rate, ...
_COLUMN_NAMES = ["calc_time", "funding_interval_hours", "last_funding_rate"]


@register("fundingRate")
class FundingRateConverter:
    """fundingRate CSV (no header) → NT FundingRateUpdate."""

    supports_chunked = False

    def validate_schema(self, df: pd.DataFrame) -> None:
        if len(df.columns) < 3:
            raise SchemaError(
                f"fundingRate CSV requires at least 3 columns, got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        from nautilus_trader.model.data import FundingRateUpdate

        # Assign column names if integer columns (no-header read)
        if len(df.columns) >= len(_COLUMN_NAMES):
            df = df.iloc[:, : len(_COLUMN_NAMES)].copy()
            df.columns = _COLUMN_NAMES
        elif isinstance(df.columns[0], int):
            cols = _COLUMN_NAMES[: len(df.columns)]
            df = df.copy()
            df.columns = cols

        records: list[FundingRateUpdate] = []
        for row in df.itertuples(index=False):
            funding_time_ms = int(row.calc_time)
            ts_ns = funding_time_ms * 1_000_000
            interval_minutes = int(row.funding_interval_hours) * 60
            records.append(FundingRateUpdate(
                instrument_id=instrument.id,
                rate=Decimal(str(row.last_funding_rate)),
                ts_event=ts_ns,
                ts_init=ts_ns,
                interval=interval_minutes,
            ))

        logger.debug(
            "Converted %d fundingRate rows to %d FundingRateUpdate objects",
            len(df), len(records),
        )
        return records

    def convert_chunk(self, chunk: pd.DataFrame, instrument: Any, **kwargs) -> list:
        return self.convert(chunk, instrument, **kwargs)
