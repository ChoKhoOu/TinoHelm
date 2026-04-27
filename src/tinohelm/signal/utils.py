"""Shared helpers for the signal framework.

Extracted from ``signal/worker.py`` and ``nt_adapter/signal_driven_strategy.py``
to avoid duplicated :class:`SignalSpec` reconstruction logic.
"""
from __future__ import annotations

from tinohelm.signal.types import CostModel, SignalSpec


def validate_supported_signal_execution(spec: SignalSpec) -> None:
    """Raise if a SignalSpec uses execution knobs not wired into kernels yet.

    ``SignalSpec`` already exposes future weighting regimes and a turnover
    budget field so configs can be forward-compatible, but the current signal
    worker and default NT strategy only execute the kernel's native
    equal-weight output plus gross/net/max-position constraints.  Failing
    loudly at run/export/start boundaries is safer than completing a run
    whose reported config was silently ignored.
    """
    if spec.weighting != "equal":
        raise ValueError(f"unsupported signal weighting: {spec.weighting}")
    if spec.turnover_budget is not None:
        raise ValueError("turnover_budget is not enforced yet")


def signal_spec_from_dict(name: str, config: dict) -> SignalSpec:
    """Reconstruct a :class:`SignalSpec` from a flat config dict.

    Shared by:

    * :mod:`tinohelm.signal.worker` — rebuilds the spec from
      ``signal_runs.config`` JSONB when processing a queued job.
    * :mod:`tinohelm.nt_adapter.signal_driven_strategy` — rebuilds the spec
      from an inline JSON payload supplied via ``signal_spec_json``.

    The dict shape is the same in both callers: flat scalar fields + a
    ``method_params`` sub-dict + a ``cost_model`` sub-dict, mirroring the
    :class:`SignalSpec` dataclass layout.

    Parameters
    ----------
    name:
        Signal identifier — used as ``SignalSpec.name`` when the config dict
        does not contain an explicit ``"name"`` key (e.g. worker path where
        the name is stored separately from the config blob).
    config:
        Flat config dict.  Unknown keys are silently ignored to stay
        forward-compatible with older persisted records.

    Returns
    -------
    SignalSpec
        Fully populated spec with defaults applied for any missing keys.
    """
    cost_dict = config.get("cost_model") or {}
    cost_model = CostModel(
        name=cost_dict.get("name", "taker_8bps"),
        fee_bps_per_side=float(cost_dict.get("fee_bps_per_side", 4.0)),
        slippage_bps_per_side=float(cost_dict.get("slippage_bps_per_side", 1.0)),
        rebate_bps_per_side=float(cost_dict.get("rebate_bps_per_side", 0.0)),
    )

    return SignalSpec(
        name=config.get("name", name),
        factor_ref=config.get("factor_ref", ""),
        method=config.get("method", "top_k_long_short"),
        weighting=config.get("weighting", "equal"),
        rebalance_freq=config.get("rebalance_freq", "1D"),
        universe_ref=config.get("universe_ref", ""),
        gross_exposure=float(config.get("gross_exposure", 1.0)),
        net_exposure=float(config.get("net_exposure", 0.0)),
        max_position=float(config.get("max_position", 0.10)),
        turnover_budget=config.get("turnover_budget"),
        method_params=dict(config.get("method_params") or {}),
        cost_model=cost_model,
        extra_warmup_bars=int(config.get("extra_warmup_bars", 0)),
        version=config.get("version", "1.0.0"),
        code_hash=config.get("code_hash", ""),
        description=config.get("description", ""),
        deprecated=bool(config.get("deprecated", False)),
    )


__all__ = ["signal_spec_from_dict", "validate_supported_signal_execution"]
