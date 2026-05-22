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

        df["calc_time"] = pd.to_numeric(df["calc_time"], errors="coerce")
        df["funding_interval_hours"] = pd.to_numeric(df["funding_interval_hours"], errors="coerce")
        df["last_funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce")
        df = df[["calc_time", "funding_interval_hours", "last_funding_rate"]].dropna().sort_values(by="calc_time")
        df = df[df["funding_interval_hours"] > 0]
        df = df.drop_duplicates(subset=["calc_time"], keep="last")

        if df.empty:
            logger.warning("fundingRate DataFrame empty after processing")
            return []

        records: list[FundingRateUpdate] = []
        for calc_time, funding_interval_hours, last_funding_rate in df.itertuples(index=False, name=None):
            funding_time_ms = int(calc_time)
            ts_ns = funding_time_ms * 1_000_000
            interval_minutes = int(funding_interval_hours) * 60
            records.append(FundingRateUpdate(
                instrument_id=instrument.id,
                rate=Decimal(str(last_funding_rate)),
                ts_event=ts_ns,
                ts_init=ts_ns,
                interval=interval_minutes,
            ))

        logger.debug(
            "Converted %d fundingRate rows to %d FundingRateUpdate objects",
            len(df), len(records),
        )
        return records

