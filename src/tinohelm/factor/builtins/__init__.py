"""Built-in declarative factors for TinoHelm.

Imports all six sub-modules so that the Registry scanner can discover
every ``@factor``-decorated function through ``pkgutil.iter_modules``.

Sub-modules
-----------
- momentum          : ret_N, rsi_signal
- volatility        : parkinson_vol, vol_ratio
- volume            : obv_slope, vwap_dev
- microstructure    : trade_imbalance, amihud_illiq
- crypto_funding    : funding_rate_level, funding_rate_mom
- crypto_data       : oi_change, orderbook_imbalance_L1
"""
from tinohelm.factor.builtins import (  # noqa: F401
    crypto_data,
    crypto_funding,
    microstructure,
    momentum,
    volatility,
    volume,
)

__all__ = [
    "crypto_data",
    "crypto_funding",
    "microstructure",
    "momentum",
    "volatility",
    "volume",
]
