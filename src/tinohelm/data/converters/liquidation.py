"""liquidationSnapshot converter — data type NOT available on Vision.

Verified 2026-04: data.binance.vision has zero files under
data/futures/um/daily/liquidationSnapshot/. This converter is kept
as a registered stub so get_converter("liquidationSnapshot") returns
a clear error instead of ValueError("Unknown data_type").
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tinohelm.data.converters import register

logger = logging.getLogger(__name__)


@register("liquidationSnapshot")
class LiquidationConverter:
    """Stub — liquidationSnapshot is not available on Binance Vision."""

    supports_chunked = False

    def validate_schema(self, df: pd.DataFrame) -> None:
        pass

    def convert(self, df: pd.DataFrame, instrument: Any, **kwargs) -> list:
        raise NotImplementedError(
            "liquidationSnapshot data is not available on "
            "data.binance.vision (verified 2026-04)."
        )

    def convert_chunk(
        self, chunk: pd.DataFrame, instrument: Any, **kwargs,
    ) -> list:
        raise NotImplementedError(
            "liquidationSnapshot data is not available on "
            "data.binance.vision (verified 2026-04)."
        )
