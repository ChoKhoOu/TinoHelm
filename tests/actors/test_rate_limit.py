"""Tests for :class:`tinohelm.node.actors.rate_limit.TokenBucket`.

The bucket replaces SnapshotActor's inline rate limiter. Its contract with
``_RedisLogHandler`` matters for production because exceeding the rate silently
drops log records (to keep Redis traffic bounded during log storms), and the
first-emit "lazy prime" behaviour is part of how the handler avoids a burst
of forwarded INFOs on startup. These tests pin those semantics.
"""
from __future__ import annotations

import pytest

from tinohelm.node.actors.rate_limit import TokenBucket


class _FakeClock:
    """Injectable monotonic clock for deterministic time-based tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class TestTokenBucketConstruction:
    def test_zero_rate_raises(self):
        with pytest.raises(ValueError, match="rate_limit must be positive"):
            TokenBucket(0)

    def test_negative_rate_raises(self):
        with pytest.raises(ValueError, match="rate_limit must be positive"):
            TokenBucket(-1)

    def test_positive_rate_accepts(self):
        tb = TokenBucket(10)
        assert tb.rate_limit == 10.0
        assert tb.tokens == 10.0

    def test_rate_limit_is_float(self):
        tb = TokenBucket(5)
        assert isinstance(tb.rate_limit, float)


class TestTokenBucketFirstCall:
    """The first try_consume primes the clock without granting a pent-up burst.

    ``_last_refill`` starts at 0.0 — if the first call computed elapsed
    against 0.0 against a real monotonic clock (typically seconds since boot
    like 12345.6), the bucket would be flooded. Hence the "prime on first
    call" logic. These tests pin that.
    """

    def test_first_call_sees_priming_elapsed_of_zero(self):
        clock = _FakeClock(start=99999.0)
        tb = TokenBucket(10, clock=clock)
        assert tb.try_consume() is True
        # Starting tokens were 10.0, priming added 0 (first-call behaviour),
        # one consumed → 9.0
        assert tb.tokens == pytest.approx(9.0)

    def test_first_call_with_zero_clock_also_primes(self):
        """Edge case: even if the injected clock is exactly 0.0, first call
        is still the priming call (since _last_refill was already 0.0)."""
        clock = _FakeClock(start=0.0)
        tb = TokenBucket(10, clock=clock)
        assert tb.try_consume() is True
        assert tb.tokens == pytest.approx(9.0)


class TestTokenBucketSteadyState:
    def test_consume_within_capacity(self):
        clock = _FakeClock()
        tb = TokenBucket(3, clock=clock)
        assert tb.try_consume() is True
        assert tb.try_consume() is True
        assert tb.try_consume() is True
        # Bucket empty now
        assert tb.try_consume() is False

    def test_refills_after_time_passes(self):
        clock = _FakeClock()
        tb = TokenBucket(10, clock=clock)
        # Prime clock
        tb.try_consume()
        # Drain the rest
        for _ in range(9):
            assert tb.try_consume() is True
        assert tb.try_consume() is False  # drained

        # 0.5s later, we should have 5 tokens
        clock.advance(0.5)
        for _ in range(5):
            assert tb.try_consume() is True
        assert tb.try_consume() is False

    def test_capacity_caps_accumulated_tokens(self):
        """Long idle period doesn't grow tokens past capacity (= rate_limit)."""
        clock = _FakeClock()
        tb = TokenBucket(5, clock=clock)
        tb.try_consume()  # prime + consume 1 → 4 tokens
        clock.advance(1000.0)  # idle for a very long time
        # Capacity caps at 5, not 5 + 1000*5 = 5005
        assert tb.tokens == pytest.approx(4.0)  # not yet refilled
        tb.try_consume()  # refill happens here → min(5, 4 + 1000*5) = 5
        # After refill to 5 and consuming 1 → 4
        assert tb.tokens == pytest.approx(4.0)

    def test_fractional_refill(self):
        clock = _FakeClock()
        tb = TokenBucket(10, clock=clock)
        # Prime
        tb.try_consume()
        assert tb.tokens == pytest.approx(9.0)
        # 0.1s passes → +1 token → 10 (capped)
        clock.advance(0.1)
        tb.try_consume()
        # After refill: min(10, 9 + 0.1*10) = 10 then -1 = 9
        assert tb.tokens == pytest.approx(9.0)

    def test_sub_unit_cost_not_granted(self):
        clock = _FakeClock()
        tb = TokenBucket(1, clock=clock)
        tb.try_consume()  # prime + -1 → 0
        clock.advance(0.5)  # not enough for 1 full token
        assert tb.try_consume() is False  # 0 + 0.5 = 0.5 < 1

    def test_custom_cost(self):
        """cost=2.5 consumes two-and-a-half tokens."""
        clock = _FakeClock()
        tb = TokenBucket(10, clock=clock)
        tb.try_consume(cost=1.0)  # prime, 9 left
        assert tb.try_consume(cost=2.5) is True
        assert tb.tokens == pytest.approx(6.5)

    def test_cost_larger_than_capacity_denied(self):
        clock = _FakeClock()
        tb = TokenBucket(5, clock=clock)
        assert tb.try_consume(cost=10.0) is False


class TestTokenBucketClockDefaults:
    """The default clock source is time.monotonic — validate it works end-to-end."""

    def test_default_clock_runs(self):
        tb = TokenBucket(100)  # High enough capacity for the small burst
        for _ in range(50):
            assert tb.try_consume() is True  # monotonic time only advances


class TestTokenBucketProductionParity:
    """Lock-in test: reproduce the exact behaviour of the original inline
    ``_RedisLogHandler.emit`` code path removed by this refactor.

    Original code (pre-refactor):
        now = _time.monotonic()
        if self._last_refill == 0.0:
            self._last_refill = now
        elapsed = now - self._last_refill
        self._tokens = min(self._rate_limit, self._tokens + elapsed * self._rate_limit)
        self._last_refill = now
        if self._tokens < 1:
            return                # reject
        self._tokens -= 1         # accept
    """

    def test_10_rps_burst_of_15_drops_5(self):
        """Under a 10 rps cap, 15 emits in < 0.1s should yield 10 accepts."""
        clock = _FakeClock()
        tb = TokenBucket(10, clock=clock)
        accepted = sum(1 for _ in range(15) if tb.try_consume())
        assert accepted == 10

    def test_steady_rate_matches_limit(self):
        """At the steady-state rate, no drops should ever happen."""
        clock = _FakeClock()
        tb = TokenBucket(10, clock=clock)
        for _ in range(100):
            assert tb.try_consume() is True
            clock.advance(0.1)  # one token per 0.1s = exactly 10 rps

    def test_double_rate_drops_half(self):
        """Emits twice the limit — roughly half get dropped.

        Bucket starts full (10 tokens) so there's a one-time burst advantage:
        the first ~19 emits are accepted back-to-back (consuming the bucket
        faster than it refills at 10 rps during 20 rps offered load), after
        which the steady-state is 1-accept-per-2-emits. Over 10 s at 20 rps
        offered: 19 initial accepts + ~90 steady-state = ~109.
        """
        clock = _FakeClock()
        tb = TokenBucket(10, clock=clock)
        accepted = 0
        for _ in range(200):  # 200 emits over 10 seconds (20 rps)
            if tb.try_consume():
                accepted += 1
            clock.advance(0.05)  # 20 per second
        # 10 s * 10 rps = 100 steady-state accepts + initial burst ≈ 109
        assert 100 <= accepted <= 115
