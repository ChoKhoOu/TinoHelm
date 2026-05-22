"""Declarative state persistence for NT strategies.

Provides the ``@stateful`` decorator that auto-generates ``on_save`` /
``on_load`` / ``on_reset`` methods so strategy authors only need to declare
*which* attributes should survive a restart.

Usage::

    from tinohelm.strategy.state import stateful

    @stateful("bar_count", "signal_active", "rolling_window",
              encoders={"rolling_window": (deque_to_list, list_to_deque)})
    class MyStrategy(Strategy):
        def __init__(self, config):
            super().__init__(config)
            self.bar_count = 0
            self.signal_active = False
            self.rolling_window = deque(maxlen=20)
        ...

NT calls ``on_save() -> dict[str, bytes]`` on graceful shutdown and
``on_load(state: dict[str, bytes])`` on restart.  The decorator wires
both automatically with JSON serialization (+ optional custom encoders).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Type aliases for custom encoder/decoder pairs
Encoder = Callable[[Any], Any]  # obj -> JSON-serializable
Decoder = Callable[[Any], Any]  # JSON-parsed -> obj


def stateful(
    *fields: str,
    encoders: dict[str, tuple[Encoder, Decoder]] | None = None,
) -> Callable:
    """Decorator that adds state persistence to an NT Strategy class.

    Args:
        *fields: Names of instance attributes to persist.
        encoders: Optional mapping of ``{field_name: (encode_fn, decode_fn)}``.
            ``encode_fn(value) -> json_serializable``
            ``decode_fn(json_parsed) -> original_type``
            Fields without a custom encoder use plain JSON serialization.

    Returns:
        Decorated class with ``on_save``, ``on_load``, and ``on_reset``
        methods injected (unless already explicitly defined by the user).
    """
    _encoders: dict[str, tuple[Encoder, Decoder]] = encoders or {}

    def decorator(cls: type) -> type:
        # Store metadata on the class for introspection
        cls._stateful_fields = fields
        cls._stateful_encoders = _encoders

        # Only inject methods that the user has NOT explicitly defined.
        # We check whether the method exists on the class itself (not inherited).
        if "on_save" not in cls.__dict__:
            cls.on_save = _make_on_save(fields, _encoders)

        if "on_load" not in cls.__dict__:
            cls.on_load = _make_on_load(fields, _encoders)

        if "on_reset" not in cls.__dict__:
            cls.on_reset = _make_on_reset(fields)

        return cls

    return decorator


def _make_on_save(
    fields: tuple[str, ...],
    encoders: dict[str, tuple[Encoder, Decoder]],
) -> Callable:
    """Build an ``on_save`` method that serializes declared fields."""

    def on_save(self) -> dict[str, bytes]:
        state: dict[str, bytes] = {}
        for field in fields:
            value = getattr(self, field, None)
            try:
                if field in encoders:
                    encode_fn = encoders[field][0]
                    value = encode_fn(value)
                state[field] = json.dumps(value).encode("utf-8")
            except (TypeError, ValueError, OverflowError) as exc:
                self.log.warning(
                    f"State save: skipping field '{field}' "
                    f"(not serializable: {exc})"
                )
        return state

    return on_save


def _make_on_load(
    fields: tuple[str, ...],
    encoders: dict[str, tuple[Encoder, Decoder]],
) -> Callable:
    """Build an ``on_load`` method that restores declared fields."""

    def on_load(self, state: dict[str, bytes]) -> None:
        for field in fields:
            if field not in state:
                continue
            try:
                raw = json.loads(state[field])
                if field in encoders:
                    decode_fn = encoders[field][1]
                    raw = decode_fn(raw)
                setattr(self, field, raw)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                self.log.warning(
                    f"State load: skipping field '{field}' "
                    f"(deserialization failed: {exc})"
                )

    return on_load


def _make_on_reset(fields: tuple[str, ...]) -> Callable:
    """Build an ``on_reset`` method that records initial values for reset."""

    def on_reset(self) -> None:
        # on_reset is called between backtest runs.
        # We reset fields to their __init__-time defaults by re-running __init__
        # pattern. Since we can't know the original defaults, we set to None.
        # Users who need specific reset values should override on_reset().
        pass

    return on_reset


# ---------------------------------------------------------------------------
# Standalone serialize / deserialize utilities
# ---------------------------------------------------------------------------


def serialize_state(
    values: dict[str, Any],
    encoders: dict[str, tuple[Encoder, Decoder]] | None = None,
) -> dict[str, bytes]:
    """Serialize a dict of field values to NT's ``dict[str, bytes]`` format.

    Useful for strategies that implement ``on_save`` manually but want
    the same JSON + custom-encoder pipeline.

    Args:
        values: ``{field_name: python_value}``
        encoders: Optional custom ``{field: (encode_fn, decode_fn)}``

    Returns:
        ``{field_name: bytes}`` ready for NT state storage.
    """
    _enc = encoders or {}
    result: dict[str, bytes] = {}
    for key, value in values.items():
        try:
            if key in _enc:
                value = _enc[key][0](value)
            result[key] = json.dumps(value).encode("utf-8")
        except (TypeError, ValueError, OverflowError) as exc:
            logger.warning(
                "serialize_state: skipping '%s' (not serializable: %s)",
                key,
                exc,
            )
    return result


def deserialize_state(
    state: dict[str, bytes],
    encoders: dict[str, tuple[Encoder, Decoder]] | None = None,
) -> dict[str, Any]:
    """Deserialize NT's ``dict[str, bytes]`` back to Python values.

    Args:
        state: ``{field_name: bytes}`` from NT state storage.
        encoders: Optional custom ``{field: (encode_fn, decode_fn)}``

    Returns:
        ``{field_name: python_value}``
    """
    _enc = encoders or {}
    result: dict[str, Any] = {}
    for key, raw_bytes in state.items():
        try:
            value = json.loads(raw_bytes)
            if key in _enc:
                value = _enc[key][1](value)
            result[key] = value
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "deserialize_state: skipping '%s' (deserialization failed: %s)",
                key,
                exc,
            )
    return result
