"""Declarative factor framework for TinoHelm.

Public API
----------
Types:
    FactorSpec, InputSpec, OutputSpec, Panel, EvalConfig, EvalResult,
    DataRequest

Alias utilities:
    FIELD_ALIAS, resolve_alias
"""
from tinohelm.factor.alias import FIELD_ALIAS, resolve_alias
from tinohelm.factor.types import (
    DataRequest,
    EvalConfig,
    EvalResult,
    FactorSpec,
    InputSpec,
    OutputSpec,
    Panel,
)

__all__ = [
    "DataRequest",
    "EvalConfig",
    "EvalResult",
    "FIELD_ALIAS",
    "FactorSpec",
    "InputSpec",
    "OutputSpec",
    "Panel",
    "resolve_alias",
]
