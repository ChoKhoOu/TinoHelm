"""Factor computation backends.

Public API
----------
AbstractBackend:
    Abstract base class defining the operator interface.
PandasBackend:
    Concrete pandas-native implementation.
"""
from tinohelm.factor.backend.base import AbstractBackend
from tinohelm.factor.backend.pandas_backend import PandasBackend

__all__ = [
    "AbstractBackend",
    "PandasBackend",
]
