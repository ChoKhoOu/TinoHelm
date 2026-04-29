"""EvalConfig parsing helpers shared by factor API and worker paths.

The API receives JSON dictionaries while the evaluator consumes frozen
``EvalConfig`` dataclasses.  Keeping the reconstruction in one place prevents
new config fields from being silently dropped by one production entrypoint.
"""
from __future__ import annotations

from typing import Any

from tinohelm.factor.types import EvalConfig, WalkForwardSpec


def parse_walk_forward_spec(value: WalkForwardSpec | dict[str, Any] | None) -> WalkForwardSpec | None:
    """Parse the JSON shape for ``EvalConfig.walk_forward``.

    ``None`` / falsy values disable walk-forward evaluation.  Dict values are
    converted to ``WalkForwardSpec`` with the same defaults as the dataclass.
    """
    if value is None or value is False:
        return None
    if isinstance(value, WalkForwardSpec):
        return value
    if not isinstance(value, dict):
        raise TypeError("walk_forward must be an object or null")
    return WalkForwardSpec(
        train_bars=int(value["train_bars"]),
        test_bars=int(value["test_bars"]),
        embargo_bars=int(value.get("embargo_bars", 0)),
        purge_bars=int(value.get("purge_bars", 0)),
        step_bars=(
            int(value["step_bars"])
            if value.get("step_bars") is not None
            else None
        ),
    )


def _tuple_of_str(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(str(item) for item in value)
    except TypeError as exc:
        raise TypeError(f"{field_name} must be a string or iterable of strings") from exc


def parse_eval_config(config_dict: dict[str, Any], *, params: dict | None = None) -> EvalConfig:
    """Build an :class:`EvalConfig` from a request/DB JSON dictionary.

    This parser intentionally includes every EvalConfig field so /explore,
    /run, and the async worker preserve the same contract.
    """
    returns_kind = config_dict.get("returns_kind", "close")
    if returns_kind not in ("close", "forward_returns"):
        raise ValueError(
            "returns_kind must be either 'close' or 'forward_returns', "
            f"got {returns_kind!r}"
        )

    return EvalConfig(
        universe=tuple(config_dict.get("universe", [])),
        start=config_dict["start"],
        end=config_dict["end"],
        forward_period=int(config_dict.get("forward_period", 5)),
        quantiles=int(config_dict.get("quantiles", 5)),
        cost_bps=float(config_dict.get("cost_bps", 4.0)),
        ic_freq=str(config_dict.get("ic_freq", "D")),
        log_ret=bool(config_dict.get("log_ret", False)),
        returns_kind=returns_kind,  # type: ignore[arg-type]
        params=params if params is not None else dict(config_dict.get("params", {}) or {}),
        universe_id=config_dict.get("universe_id"),
        neutralize=_tuple_of_str(config_dict.get("neutralize"), field_name="neutralize"),
        walk_forward=parse_walk_forward_spec(config_dict.get("walk_forward")),
        segments=_tuple_of_str(config_dict.get("segments"), field_name="segments"),
    )


__all__ = ["parse_eval_config", "parse_walk_forward_spec"]
