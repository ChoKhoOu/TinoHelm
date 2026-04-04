"""metrics CSV → BinanceMetrics custom data converter.

CSV schema (HAS header row, confirmed from sample):
  create_time, symbol, sum_open_interest, sum_open_interest_value,
  count_toptrader_long_short_ratio, sum_toptrader_long_short_ratio,
  count_long_short_ratio, sum_taker_long_short_vol_ratio
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from tinohelm.data.converters import SchemaError, register

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {"create_time", "symbol", "sum_open_interest"}


@dataclass
class BinanceMetrics:
    """Binance Futures market metrics snapshot (5-min intervals)."""
    symbol: str
    open_interest: float
    open_interest_value: float
    toptrader_long_short_ratio_count: float
    toptrader_long_short_ratio_sum: float
    global_long_short_ratio: float
    taker_long_short_vol_ratio: float
    ts_event: int
    ts_init: int


@register("metrics")
class MetricsConverter:
    """Converts Binance Vision metrics CSV (has header) to BinanceMetrics.

    Note: This CSV has a header row (unlike klines/trades).
    """

    supports_chunked = False

    def validate_schema(self, df: pd.DataFrame) -> None:
        if hasattr(df.columns[0], 'lower'):
            missing = _REQUIRED_COLUMNS - set(df.columns)
            if missing:
                raise SchemaError(
                    f"metrics CSV missing columns: {missing}. "
                    f"Got: {list(df.columns)}"
                )
        elif len(df.columns) < 3:
            raise SchemaError(
                f"metrics CSV requires at least 3 columns, "
                f"got {len(df.columns)}"
            )

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        symbol: str = kwargs.get("symbol", str(instrument.id))

        records: list[BinanceMetrics] = []
        for row in df.itertuples(index=False):
            ts_str = str(row.create_time)
            ts_ns = _parse_timestamp_ns(ts_str)

            records.append(BinanceMetrics(
                symbol=symbol,
                open_interest=float(row.sum_open_interest),
                open_interest_value=float(
                    row.sum_open_interest_value,
                ),
                toptrader_long_short_ratio_count=float(
                    getattr(
                        row, 'count_toptrader_long_short_ratio', 0,
                    ),
                ),
                toptrader_long_short_ratio_sum=float(
                    getattr(
                        row, 'sum_toptrader_long_short_ratio', 0,
                    ),
                ),
                global_long_short_ratio=float(
                    getattr(row, 'count_long_short_ratio', 0),
                ),
                taker_long_short_vol_ratio=float(
                    getattr(
                        row, 'sum_taker_long_short_vol_ratio', 0,
                    ),
                ),
                ts_event=ts_ns,
                ts_init=ts_ns,
            ))

        logger.debug(
            "Converted %d metrics rows to %d objects",
            len(df), len(records),
        )
        return records

    def convert_chunk(
        self, chunk: pd.DataFrame, instrument: Any, **kwargs,
    ) -> list:
        return self.convert(chunk, instrument, **kwargs)


def _parse_timestamp_ns(ts_str: str) -> int:
    """Parse timestamp to nanoseconds.

    Handles both ms integers and datetime strings like
    '2025-01-01 00:05:00'.
    """
    try:
        ms = int(ts_str)
        return ms * 1_000_000
    except ValueError:
        dt = pd.Timestamp(ts_str, tz="UTC")
        return int(dt.timestamp() * 1_000_000_000)
