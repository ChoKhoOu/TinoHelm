"""Factor computation backends.

Public API
----------
AbstractBackend:
    :class:`typing.Protocol` (``runtime_checkable``) defining the operator
    interface.  Implementations need not subclass it explicitly.
PolarsBackend:
    Concrete polars-native implementation.  This is the canonical backend
    used by the factor engine.
"""
from tinohelm.factor.backend.base import AbstractBackend
from tinohelm.factor.backend.polars_backend import PolarsBackend

__all__ = [
    "AbstractBackend",
    "PolarsBackend",
]
