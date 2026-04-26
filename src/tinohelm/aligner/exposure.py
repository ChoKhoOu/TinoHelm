"""ExposureProvider Protocol and builtin provider imports.

The Protocol definition lives here.  Concrete implementations are in separate
modules to keep this file focused on the public contract:

- ``exposure_btc.py``    — BTCBetaExposure
- ``exposure_logmcap.py`` — LogMcapExposure

All three are re-exported here so callers can import from a single location::

    from tinohelm.aligner.exposure import ExposureProvider, BTCBetaExposure, LogMcapExposure
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import polars as pl

from tinohelm.aligner.exposure_btc import BTCBetaExposure
from tinohelm.aligner.exposure_logmcap import LogMcapExposure


@runtime_checkable
class ExposureProvider(Protocol):
    """Middle-layer neutralisation extension point — provides a (T, N) exposure panel.

    Implementors must expose a ``name: str`` class-level or instance attribute;
    Aligner uses it to match the string references in EvalConfig.neutralize.

    PIT guarantee: ``get_exposure`` must return PIT-aligned data; any look-ahead
    (using future data to estimate the current exposure) is the responsibility of
    the implementing class.
    """

    name: str

    def get_exposure(
        self,
        timestamps: pl.Series,  # Datetime series, length T
        symbols: list[str],     # length N
    ) -> pl.DataFrame:
        """Return wide-format DataFrame: first column ``ts`` (Datetime), then N
        symbol columns; row count equals T.
        """
        ...


__all__ = [
    "ExposureProvider",
    "BTCBetaExposure",
    "LogMcapExposure",
]
