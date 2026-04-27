"""Canonical msgbus topic names for TinoHelm node communication.

Two-prefix ownership model:

Detection topics (published by RiskGuardActor):
    ``RISK_GUARD_STATE``   — breach action string ("halt_new", "reduce_only", "flatten_all")
    ``RISK_GUARD_FLATTEN`` — instrument ID string for per-instrument flatten

Control topics (published by LifecycleController):
    ``LIFECYCLE_PAUSE``    — template: f"{LIFECYCLE_PAUSE}.{strategy_id}"
    ``LIFECYCLE_RESUME``   — template: f"{LIFECYCLE_RESUME}.{strategy_id}"
    ``LIFECYCLE_FLATTEN``  — flatten command
"""
from __future__ import annotations

# Detection (RiskGuardActor -> subscribers)
RISK_GUARD_STATE = "risk.guard.state"
RISK_GUARD_FLATTEN = "risk.guard.flatten"

# Control (LifecycleController -> strategies/system)
LIFECYCLE_PAUSE = "lifecycle.pause"      # Usage: f"{LIFECYCLE_PAUSE}.{strategy_id}"
LIFECYCLE_RESUME = "lifecycle.resume"    # Usage: f"{LIFECYCLE_RESUME}.{strategy_id}"
LIFECYCLE_FLATTEN = "lifecycle.flatten"

# Signal commission monitoring (MetricsActor -> Redis PubSub)
# Redis channel format: f"tino:{node_type}:{SIGNAL_COMMISSION_DEVIATION}"
#
# Scope: This monitor only validates *exchange commission per fill*. It does
# NOT include slippage or maker rebate — see :class:`tinohelm.signal.types.CostModel`
# for the full research-side cost definition (fee + slippage − rebate).
# Tracked as follow-up: extending live monitor to full fee+slippage−rebate
# requires expected_px context + venue rebate data source.
SIGNAL_COMMISSION_DEVIATION = "signal.commission.deviation"

# DEPRECATED: alias for SIGNAL_COMMISSION_DEVIATION. Removed after one
# release cycle. Subscribers should migrate to SIGNAL_COMMISSION_DEVIATION /
# the ``signal.commission.deviation`` channel suffix.
SIGNAL_COST_DEVIATION = "signal.cost.deviation"
