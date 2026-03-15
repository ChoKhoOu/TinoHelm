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
