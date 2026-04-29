"""Declarative signal framework for TinoHelm.

A signal is the bridge between a *factor* (a cross-section signal value
panel) and a *portfolio weight panel* — it consumes
``factor_panel: pl.DataFrame`` and emits ``weight_panel: pl.DataFrame``
that respects gross/net/per-asset position constraints.

Public API
----------
Types:
    SignalSpec, CostModel, SignalMethod, SignalWeighting, CostModelName

Decorator / registry:
    signal, SignalRegistry

Kernel helpers:
    normalize_to_constraints, split_factor_panel, build_weight_panel

Built-in kernels:
    top_k_long_short, quantile_long_short, threshold_signed,
    zscore_clip, rank_to_weight
"""
from tinohelm.signal.decorator import signal
from tinohelm.signal.kernel import (
    build_weight_panel,
    normalize_to_constraints,
    split_factor_panel,
)
from tinohelm.signal.kernels import (
    quantile_long_short,
    rank_to_weight,
    threshold_signed,
    top_k_long_short,
    zscore_clip,
)
from tinohelm.signal.registry import SignalRegistry
from tinohelm.signal.types import (
    CostModel,
    CostModelName,
    SignalMethod,
    SignalSpec,
    SignalWeighting,
)

__all__ = [
    "CostModel",
    "CostModelName",
    "SignalMethod",
    "SignalRegistry",
    "SignalSpec",
    "SignalWeighting",
    "build_weight_panel",
    "normalize_to_constraints",
    "quantile_long_short",
    "rank_to_weight",
    "signal",
    "split_factor_panel",
    "threshold_signed",
    "top_k_long_short",
    "zscore_clip",
]
