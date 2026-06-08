"""Warmup-live parity guard for OI poll HOUR ALIGNMENT (Hard Rule #2).

In the TinoHelm deployment the 30d OI warmup is fetched over REST from
``/futures/data/openInterestHist?period=1h`` — whose readings are ALREADY integer-hour
bucket labels (:00:00) — and seeds the same raw-OI buffer that the live steady-state poll
feeds. The live poll instead hits ``/fapi/v1/openInterest``, which returns a CONTINUOUS
real-time value (NOT a bucket), at some wall-clock offset after the hour. Two things must
hold for the live series to stay byte-identical to the warmup grid (and thus the backtest):

  1. The OIFeedActor must poll INSIDE the first 5-minute window after the hour (so the live
     value still belongs to the ``:00`` bucket — verified on real PAXG: 0-4.9min → 0 ARM
     flips vs backtest; >=5min drifts), driven by ``OIFeedConfig.poll_offset_secs`` (120s).
  2. ``strategy._append_oi_reading`` must FLOOR the reading's ts to the top of the hour
     before buffering, so ``compute_oi_signal_grid``'s ``resample("1h", label="right")``
     stamps it on hour H — not hour H+1 (an un-floored :02:03 lands in (H:00, H+1:00] →
     labeled H+1:00, a one-hour phase lead; measured 2.42% ARM-flip divergence before this
     fix). Flooring is identity on the already-:00 warmup readings, so one floor handles
     both sources.

These tests pin both halves. They are the regression guard for the phase-alignment gap.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd

from strategies.oi_momentum_lowvol.rolling_oi_threshold import compute_oi_signal_grid

WIN = 3
ROLL = 30 * 96
PCT = 0.98
_HOUR_NS = 3_600 * 1_000_000_000


def _floor_to_hour(ts_ns: int) -> int:
    """The exact expression strategy._append_oi_reading uses."""
    return int(ts_ns) - (int(ts_ns) % _HOUR_NS)


# ───────────────────────── 1. floor math ───────────────────────── #


def test_floor_to_hour_math():
    base = pd.Timestamp("2026-06-05 14:00:00", tz="UTC").value
    for off_min, off_s in [(2, 0), (2, 3), (0, 7), (4, 59), (59, 59)]:
        ts = base + off_min * 60 * 1_000_000_000 + off_s * 1_000_000_000
        assert _floor_to_hour(ts) == base, f"floor failed at +{off_min}m{off_s}s"
    # A reading already on the hour is unchanged.
    assert _floor_to_hour(base) == base
    # The next hour floors to the next hour, not this one.
    assert _floor_to_hour(base + _HOUR_NS + 5) == base + _HOUR_NS


def test_append_oi_reading_source_floors_ts():
    """The deployed _append_oi_reading must contain the hour-floor before the dedup guard."""
    from strategies.oi_momentum_lowvol.strategy import OIMomentum

    src = inspect.getsource(OIMomentum._append_oi_reading)
    assert "% (3_600 * 1_000_000_000)" in src, (
        "_append_oi_reading lost its hour-floor — live OI phase will drift vs warmup."
    )
    # The floor must precede the monotonic dedup guard (else a sub-hour second poll that
    # floors onto the last ts would not be dropped correctly).
    floor_pos = src.index("% (3_600 * 1_000_000_000)")
    guard_pos = src.index("ts_ns <= state._oi_raw_ts[-1]")
    assert floor_pos < guard_pos, "hour-floor must come before the dedup guard"


# ─────────────────── 2. timer alignment in the actor ─────────────────── #


def test_actor_poll_timer_aligned_to_hour():
    """OIFeedActor.on_start must compute an oi_poll start_time at the next hour + offset."""
    from strategies.oi_momentum_lowvol.oi_feed_actor import OIFeedActor, OIFeedConfig

    src = inspect.getsource(OIFeedActor.on_start)
    assert "replace(minute=0, second=0, microsecond=0)" in src, (
        "oi_poll timer is no longer hour-aligned — live poll phase will be random again."
    )
    assert "poll_offset_secs" in src and "start_time=" in src, (
        "oi_poll timer must pass start_time = next_hour + poll_offset_secs."
    )
    # Default offset lands safely inside the first 5min bucket (clear of :00, well before :05).
    assert 0 < OIFeedConfig(symbols=["BTCUSDT"]).poll_offset_secs < 300


# ───────────── 3. end-to-end: live (floored poll) == backtest (5m bucket) ───────────── #


def _five_min_oi(days: int = 60, seed: int = 0) -> pd.Series:
    """Synthetic 5m OI level (random walk), mimicking the vision parquet cadence."""
    idx = pd.date_range("2025-01-01", periods=days * 24 * 12, freq="5min", tz="UTC")
    rng = np.random.RandomState(seed)
    level = np.cumsum(rng.randn(len(idx))) * 500 + 1_000_000
    return pd.Series(np.clip(level, 1.0, None), index=idx)


def _grid(series: pd.Series):
    return compute_oi_signal_grid(
        series, delta_window_bars=WIN, rolling_threshold_bars=ROLL, threshold_percentile=PCT
    )


def _live_poll_floored(oi5: pd.Series, offset_min: float) -> pd.Series:
    """Mimic the live path: hourly poll at the hour+offset taking the last 5m value <= the
    poll instant (the continuous /openInterest reading), with its ts FLOORED to the hour."""
    t0 = oi5.index[0].floor("h") + pd.Timedelta(minutes=offset_min)
    polls = pd.date_range(t0, oi5.index[-1], freq="1h")
    m = pd.merge_asof(
        pd.DataFrame({"t": polls}),
        pd.DataFrame({"ts": oi5.index, "v": oi5.to_numpy()}),
        left_on="t",
        right_on="ts",
        direction="backward",
    )
    floored = polls.floor("h")
    s = pd.Series(m["v"].to_numpy(), index=floored).dropna()
    return s[~s.index.duplicated(keep="last")]


def _arm_flip_count(g_bt, g_live) -> tuple[int, int]:
    j = pd.concat(
        {
            "bt_d": g_bt["oi_delta"],
            "bt_t": g_bt["threshold"],
            "lv_d": g_live["oi_delta"],
            "lv_t": g_live["threshold"],
        },
        axis=1,
        join="inner",
    )
    arm_bt = (j["bt_d"] >= j["bt_t"]) & np.isfinite(j["bt_t"])
    arm_lv = (j["lv_d"] >= j["lv_t"]) & np.isfinite(j["lv_t"])
    return int((arm_bt != arm_lv).sum()), len(j)


def test_in_window_poll_matches_backtest_byte_identical():
    """A poll anywhere in [:00, :05) + hour-floor → byte-identical grid vs the 5m-bucket
    backtest. This is the core parity assertion (real-PAXG verified at 0/7194 flips)."""
    oi5 = _five_min_oi(days=60, seed=1)
    g_bt = _grid(oi5)
    for off in [0.5, 2.0, 4.9]:  # default 120s = 2.0min; bracket the safe window
        g_live = _grid(_live_poll_floored(oi5, off))
        j = pd.concat({"a": g_bt["oi_delta"], "b": g_live["oi_delta"]}, axis=1, join="inner")
        assert np.allclose(j["a"], j["b"], atol=1e-12), f"oi_delta drift at +{off}min"
        flips, n = _arm_flip_count(g_bt, g_live)
        assert flips == 0, f"+{off}min: {flips}/{n} ARM flips (expected 0 inside the window)"


def test_out_of_window_poll_diverges_negative_control():
    """Negative control: a poll at :15 (random-phase, the OLD behaviour) DOES diverge —
    proving the in-window assertion above is actually testing phase alignment, not a no-op."""
    oi5 = _five_min_oi(days=60, seed=1)
    g_bt = _grid(oi5)
    # :15 with NO floor (old behaviour: ts kept, lands in next bucket) — emulate by not
    # flooring: build the live series at the raw poll ts.
    t0 = oi5.index[0].floor("h") + pd.Timedelta(minutes=15)
    polls = pd.date_range(t0, oi5.index[-1], freq="1h")
    m = pd.merge_asof(
        pd.DataFrame({"t": polls}),
        pd.DataFrame({"ts": oi5.index, "v": oi5.to_numpy()}),
        left_on="t",
        right_on="ts",
        direction="backward",
    )
    g_live = _grid(pd.Series(m["v"].to_numpy(), index=polls).dropna())  # raw :15 ts, no floor
    flips, _ = _arm_flip_count(g_bt, g_live)
    assert flips > 0, (
        "the old random-phase un-floored path matched the backtest — the negative control is "
        "broken, so the alignment test no longer proves anything."
    )
