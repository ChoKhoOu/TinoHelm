"""Pure token-bucket rate limiter for log forwarding and other throttled sinks.

Extracted from SnapshotActor's inline rate-limiter so the algorithm can be
tested with an injected monotonic clock and reused by any other sink that
needs to drop messages above a steady-state rate. The original handler's
behaviour is preserved exactly: ``rate_limit`` is both the bucket capacity
and the token refill rate per second, and the first call primes the clock
rather than granting a full bucket immediately.
"""
from __future__ import annotations

import time as _time
from typing import Callable


class TokenBucket:
    """Simple token bucket with a configurable clock source.

    Parameters
    ----------
    rate_limit
        Tokens added per second. Also used as the bucket capacity so a quiet
        caller never accumulates more headroom than one second of traffic.
    clock
        Returns a monotonic-ish seconds float. Injectable for tests.

    The counter starts empty (``_tokens == rate_limit`` would let a caller burn
    a full second of traffic on startup before any time has elapsed, which the
    original inline handler explicitly avoids by priming on first call).
    """

    def __init__(
        self,
        rate_limit: int,
        clock: Callable[[], float] = _time.monotonic,
    ) -> None:
        if rate_limit <= 0:
            raise ValueError(f"rate_limit must be positive, got {rate_limit}")
        self._rate_limit = float(rate_limit)
        self._clock = clock
        self._tokens = float(rate_limit)
        # None sentinel (not 0.0) — the original inline handler used 0.0 which
        # works in production because time.monotonic() is never exactly 0.0
        # since system boot, but makes testing with an injected clock that
        # starts at zero impossible.
        self._last_refill: float | None = None

    @property
    def tokens(self) -> float:
        """Current token count — primarily for introspection/tests."""
        return self._tokens

    @property
    def rate_limit(self) -> float:
        return self._rate_limit

    def try_consume(self, cost: float = 1.0) -> bool:
        """Attempt to consume ``cost`` tokens. Returns True if granted.

        On the very first call, primes the refill clock and does not grant
        pent-up tokens — matches the original handler's "lazy first emit"
        semantics. Subsequent calls accrue ``elapsed * rate_limit`` tokens,
        capped at ``rate_limit``.
        """
        now = self._clock()
        if self._last_refill is None:
            self._last_refill = now
        elapsed = now - self._last_refill
        self._tokens = min(
            self._rate_limit, self._tokens + elapsed * self._rate_limit,
        )
        self._last_refill = now
        if self._tokens < cost:
            return False
        self._tokens -= cost
        return True
