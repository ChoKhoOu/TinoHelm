"""aligner — ExposureProvider protocol, builtin providers, registry, and Aligner.

Public API
----------
ExposureProvider  : runtime_checkable Protocol; implement to add a custom exposure
register          : register a custom ExposureProvider class by name
resolve           : resolve a registered name to an ExposureProvider instance
list_providers    : list all registered provider names (builtin + user)
Aligner           : apply Universe PIT mask + cross-section OLS neutralization
PITViolationError : raised when an exposure provider returns future timestamps
"""

from __future__ import annotations

from tinohelm.aligner.aligner import Aligner, PITViolationError
from tinohelm.aligner.exposure import ExposureProvider, BTCBetaExposure, LogMcapExposure
from tinohelm.aligner.registry import register, resolve, list_providers

__all__ = [
    "Aligner",
    "PITViolationError",
    "ExposureProvider",
    "BTCBetaExposure",
    "LogMcapExposure",
    "register",
    "resolve",
    "list_providers",
]
