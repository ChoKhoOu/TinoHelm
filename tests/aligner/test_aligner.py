"""Tests for tinohelm.aligner.aligner.Aligner.

Coverage
--------
1. test_string_input_resolves_to_provider
2. test_instance_input_used_directly
3. test_mixed_input
4. test_unknown_string_raises_keyerror
5. test_align_output_shape
6. test_universe_mask_applied
7. test_ols_residual_zero_for_known_exposure
8. test_pit_violation_raises
9. test_two_providers_combined_residual
10. test_no_neutralize_returns_pit_masked_panel
11. test_empty_neutralize_list
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import polars as pl
import pytest

from tinohelm.aligner.aligner import Aligner, PITViolationError
from tinohelm.aligner.exposure import BTCBetaExposure
from tinohelm.aligner import registry as _registry_module
from tinohelm.factor.universe import Universe


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

SYMBOLS = ["SYM1", "SYM2", "SYM3", "SYM4", "SYM5"]
T = 10  # number of timestamps

# Fixed timestamps — naive datetime, starting 2026-01-01
_BASE_DT = datetime(2026, 1, 1)
TIMESTAMPS = [datetime(2026, 1, i + 1) for i in range(T)]


def _make_universe(
    symbols: list[str] | None = None,
    listing_override: dict[str, datetime] | None = None,
    delisting_override: dict[str, datetime | None] | None = None,
) -> Universe:
    """Build a test Universe.

    By default all symbols have listing_date=1970-01-01 (always eligible).
    Pass ``listing_override`` / ``delisting_override`` to change per-symbol dates.
    """
    syms = symbols or SYMBOLS
    listing_override = listing_override or {}
    delisting_override = delisting_override or {}

    from tinohelm.factor.universe import _UniverseRow  # noqa: PLC0415
    rows = []
    for sym in syms:
        listing = listing_override.get(sym, datetime(1970, 1, 1))
        delisting = delisting_override.get(sym, None)
        rows.append(_UniverseRow(sym, listing, delisting))
    return Universe(rows, name="test")


def _make_panel(
    symbols: list[str] | None = None,
    timestamps: list[datetime] | None = None,
    seed: int = 42,
) -> pl.DataFrame:
    """Build a random factor panel (T, N) with Datetime ts column."""
    syms = symbols or SYMBOLS
    ts = timestamps or TIMESTAMPS
    rng = np.random.default_rng(seed)
    data: dict[str, object] = {"ts": ts}
    for sym in syms:
        data[sym] = rng.standard_normal(len(ts)).tolist()
    return pl.DataFrame(data).with_columns(pl.col("ts").cast(pl.Datetime))


class FakeExposure:
    """Controllable test ExposureProvider.

    Returns fixed ``exposure_values`` (one float per symbol per ts)
    as a wide-format DataFrame.
    """

    def __init__(
        self,
        name: str = "fake",
        exposure_values: dict[str, list[float]] | None = None,
        timestamps: list[datetime] | None = None,
        future_ts_offset: int = 0,
    ) -> None:
        self.name = name
        self._exposure_values = exposure_values  # sym -> [val_per_ts, ...]
        self._timestamps = timestamps or TIMESTAMPS
        self._future_ts_offset = future_ts_offset  # inject future ts for PIT test

    def get_exposure(
        self,
        timestamps: pl.Series,
        symbols: list[str],
    ) -> pl.DataFrame:
        ts_list: list[datetime] = timestamps.to_list()

        if self._future_ts_offset > 0:
            # Inject a future timestamp beyond max(ts_list) to trigger PIT error
            max_ts = max(ts_list)
            future = datetime(
                max_ts.year + self._future_ts_offset, max_ts.month, max_ts.day
            )
            ts_list = ts_list + [future]

        data: dict[str, object] = {"ts": ts_list}
        for sym in symbols:
            n = len(ts_list)
            if self._exposure_values and sym in self._exposure_values:
                vals = self._exposure_values[sym]
                # Pad or truncate to match n
                padded = list(vals) + [0.0] * max(0, n - len(vals))
                data[sym] = padded[:n]
            else:
                data[sym] = [1.0] * n
        return pl.DataFrame(data).with_columns(pl.col("ts").cast(pl.Datetime))


def _register_fake(name: str = "fake_for_test") -> type:
    """Register FakeExposure under *name* and return its class."""
    _registry_module._USER_PROVIDERS[name] = FakeExposure
    return FakeExposure


def _clear_user(name: str = "fake_for_test") -> None:
    _registry_module._USER_PROVIDERS.pop(name, None)


# ---------------------------------------------------------------------------
# AC-1: String/instance resolution
# ---------------------------------------------------------------------------


def test_string_input_resolves_to_provider() -> None:
    """String 'btc_beta' resolves to BTCBetaExposure via registry."""
    uni = _make_universe()
    aligner = Aligner(uni, neutralize=["btc_beta"])
    assert len(aligner._providers) == 1
    assert isinstance(aligner._providers[0], BTCBetaExposure)


def test_instance_input_used_directly() -> None:
    """An ExposureProvider instance is used as-is (identity preserved)."""
    uni = _make_universe()
    fake = FakeExposure()
    aligner = Aligner(uni, neutralize=[fake])
    assert aligner._providers[0] is fake


def test_mixed_input() -> None:
    """String + instance mix: both resolve to 2 providers."""
    _register_fake("fake_mixed")
    try:
        uni = _make_universe()
        fake = FakeExposure()
        aligner = Aligner(uni, neutralize=["fake_mixed", fake])
        assert len(aligner._providers) == 2
        # first is resolved from registry (FakeExposure instance)
        assert isinstance(aligner._providers[0], FakeExposure)
        # second is the same object
        assert aligner._providers[1] is fake
    finally:
        _clear_user("fake_mixed")


def test_unknown_string_raises_keyerror() -> None:
    """Unregistered string name raises KeyError at construction time."""
    uni = _make_universe()
    with pytest.raises(KeyError):
        Aligner(uni, neutralize=["this_does_not_exist_xyzzy"])


# ---------------------------------------------------------------------------
# AC-2: Output shape
# ---------------------------------------------------------------------------


def test_align_output_shape() -> None:
    """Output panel has the same (T, N+1) shape as the input panel."""
    uni = _make_universe()
    fake = FakeExposure()
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[fake])
    out = aligner.align(panel)
    assert out.shape == panel.shape
    assert out.columns == panel.columns


# ---------------------------------------------------------------------------
# AC-3: Universe PIT mask
# ---------------------------------------------------------------------------


def test_universe_mask_applied() -> None:
    """Cells before listing_date+7d are null in output."""
    # SYM3 lists 2026-01-05 → eligible from 2026-01-12 (+ 7 days)
    # Our timestamps run 2026-01-01 … 2026-01-10, all < 2026-01-12
    # → entire SYM3 column should be null
    listing_date = datetime(2026, 1, 5)
    uni = _make_universe(listing_override={"SYM3": listing_date})
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[])
    out = aligner.align(panel)

    sym3_col = out["SYM3"]
    assert sym3_col.null_count() > 0, "Expected at least one null in SYM3"

    # All timestamps are 2026-01-01 to 2026-01-10, eligible_from = 2026-01-12
    # so ALL should be null
    assert sym3_col.null_count() == T, (
        f"Expected all {T} SYM3 cells to be null, got {sym3_col.null_count()}"
    )

    # Other symbols should not be nulled (they have 1970-01-01 listing)
    for sym in ["SYM1", "SYM2", "SYM4", "SYM5"]:
        assert out[sym].null_count() == 0, f"{sym} should have no nulls from PIT mask"


def test_universe_mask_partial_rows() -> None:
    """Cells before listing_date+7d are null; cells after are non-null."""
    # SYM2 lists 2026-01-01 → eligible from 2026-01-08
    # timestamps 2026-01-01..07 (indices 0-6) should be null
    # timestamps 2026-01-08..10 (indices 7-9) should NOT be null
    listing_date = datetime(2026, 1, 1)
    uni = _make_universe(listing_override={"SYM2": listing_date})
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[])
    out = aligner.align(panel)

    sym2_col = out["SYM2"]
    # First 7 rows null, last 3 non-null
    assert sym2_col.null_count() == 7
    assert sym2_col[7:].null_count() == 0


# ---------------------------------------------------------------------------
# AC-4: OLS residual correctness
# ---------------------------------------------------------------------------


def test_ols_residual_zero_for_known_exposure() -> None:
    """Residuals ≈ 0 when factor = α + β × exposure (no noise factor)."""
    rng = np.random.default_rng(0)
    N = len(SYMBOLS)

    # Fixed exposure per symbol (constant across time for simplicity)
    exposure_vals = rng.standard_normal(N)
    alpha = 2.0
    beta = 3.0

    # Build panel: y = alpha + beta * exposure (no noise — residual should be 0)
    sym_data: dict[str, list[float]] = {}
    exp_data: dict[str, list[float]] = {}
    for i, sym in enumerate(SYMBOLS):
        y_vals = [alpha + beta * exposure_vals[i]] * T
        sym_data[sym] = y_vals
        exp_data[sym] = [exposure_vals[i]] * T

    panel_data: dict[str, object] = {"ts": TIMESTAMPS}
    panel_data.update(sym_data)
    panel = pl.DataFrame(panel_data).with_columns(pl.col("ts").cast(pl.Datetime))

    fake = FakeExposure(exposure_values=exp_data)
    uni = _make_universe()
    aligner = Aligner(uni, neutralize=[fake])
    out = aligner.align(panel)

    for sym in SYMBOLS:
        arr = out[sym].to_numpy()
        valid = arr[~np.isnan(arr)]
        mean_abs = float(np.abs(valid).mean()) if len(valid) > 0 else 0.0
        assert mean_abs < 1e-8, (
            f"{sym}: expected near-zero residuals, got mean_abs={mean_abs:.2e}"
        )


def test_ols_residual_small_for_noisy_exposure() -> None:
    """Residuals have small mean when panel = α + β × exposure + ε (ε tiny)."""
    rng = np.random.default_rng(1)
    N = len(SYMBOLS)

    exposure_vals = rng.standard_normal(N)
    alpha, beta = 1.5, 2.5
    eps_scale = 1e-6

    sym_data: dict[str, list[float]] = {}
    exp_data: dict[str, list[float]] = {}
    for i, sym in enumerate(SYMBOLS):
        eps = rng.standard_normal(T) * eps_scale
        y_vals = [alpha + beta * exposure_vals[i] + e for e in eps]
        sym_data[sym] = y_vals
        exp_data[sym] = [exposure_vals[i]] * T

    panel_data: dict[str, object] = {"ts": TIMESTAMPS}
    panel_data.update(sym_data)
    panel = pl.DataFrame(panel_data).with_columns(pl.col("ts").cast(pl.Datetime))

    fake = FakeExposure(exposure_values=exp_data)
    uni = _make_universe()
    aligner = Aligner(uni, neutralize=[fake])
    out = aligner.align(panel)

    all_vals = []
    for sym in SYMBOLS:
        arr = out[sym].to_numpy()
        all_vals.extend(arr[~np.isnan(arr)].tolist())

    mean_abs = float(np.abs(all_vals).mean()) if all_vals else 0.0
    assert mean_abs < 1e-3, f"Expected mean |residual| < 1e-3, got {mean_abs:.2e}"


# ---------------------------------------------------------------------------
# AC-5: PIT violation
# ---------------------------------------------------------------------------


def test_pit_violation_raises() -> None:
    """FakeExposure returning a future ts triggers PITViolationError."""
    uni = _make_universe()
    # future_ts_offset=1 appends a timestamp 1 year beyond max(panel.ts)
    bad_provider = FakeExposure(future_ts_offset=1)
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[bad_provider])
    with pytest.raises(PITViolationError):
        aligner.align(panel)


def test_pit_check_passes_when_no_future_ts() -> None:
    """Normal provider (no future ts) does not raise PITViolationError."""
    uni = _make_universe()
    provider = FakeExposure(future_ts_offset=0)
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[provider])
    # Should not raise
    out = aligner.align(panel)
    assert out.shape == panel.shape


# ---------------------------------------------------------------------------
# AC-6: Two providers combined residual
# ---------------------------------------------------------------------------


def test_two_providers_combined_residual() -> None:
    """Two providers simultaneously: residuals still near-zero for exact linear combo."""
    rng = np.random.default_rng(7)
    N = len(SYMBOLS)

    exp1 = rng.standard_normal(N)
    exp2 = rng.standard_normal(N)
    alpha, beta1, beta2 = 0.5, 1.2, -0.8

    sym_data: dict[str, list[float]] = {}
    exp1_data: dict[str, list[float]] = {}
    exp2_data: dict[str, list[float]] = {}
    for i, sym in enumerate(SYMBOLS):
        y_vals = [alpha + beta1 * exp1[i] + beta2 * exp2[i]] * T
        sym_data[sym] = y_vals
        exp1_data[sym] = [exp1[i]] * T
        exp2_data[sym] = [exp2[i]] * T

    panel_data: dict[str, object] = {"ts": TIMESTAMPS}
    panel_data.update(sym_data)
    panel = pl.DataFrame(panel_data).with_columns(pl.col("ts").cast(pl.Datetime))

    provider1 = FakeExposure(name="fake1", exposure_values=exp1_data)
    provider2 = FakeExposure(name="fake2", exposure_values=exp2_data)
    uni = _make_universe()
    aligner = Aligner(uni, neutralize=[provider1, provider2])
    out = aligner.align(panel)

    for sym in SYMBOLS:
        arr = out[sym].to_numpy()
        valid = arr[~np.isnan(arr)]
        mean_abs = float(np.abs(valid).mean()) if len(valid) > 0 else 0.0
        assert mean_abs < 1e-8, (
            f"{sym}: two-provider residual should be near-zero, got {mean_abs:.2e}"
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_no_neutralize_returns_pit_masked_panel() -> None:
    """With neutralize=[], align() only applies PIT mask — no OLS."""
    uni = _make_universe()
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[])
    out = aligner.align(panel)
    # All symbols have 1970 listing, so no PIT masking → output == input
    for sym in SYMBOLS:
        assert out[sym].null_count() == 0


def test_empty_neutralize_list() -> None:
    """Empty neutralize list is valid — no providers stored."""
    uni = _make_universe()
    aligner = Aligner(uni, neutralize=())
    assert aligner._providers == []


def test_align_preserves_null_in_input() -> None:
    """Nulls already in the panel input propagate through alignment."""
    uni = _make_universe()
    panel = _make_panel()
    # Manually inject null into row 0, SYM1
    panel = panel.with_columns(
        pl.when(pl.int_range(0, len(panel)) == 0)
        .then(None)
        .otherwise(pl.col("SYM1"))
        .alias("SYM1")
    )
    fake = FakeExposure()
    aligner = Aligner(uni, neutralize=[fake])
    out = aligner.align(panel)
    # Row 0, SYM1 should still be null
    assert out["SYM1"][0] is None


# ---------------------------------------------------------------------------
# H3 regression: ts type consistency between panel and providers
# ---------------------------------------------------------------------------


class _TzAwareExposure:
    """Test ExposureProvider that returns the same ts in tz-aware UTC form.

    Used to exercise the H3 fix: even though the panel ts is tz-naive, the
    provider's tz-aware ts column should still align after normalization.
    """

    name = "tz_aware_fake"

    def __init__(self, exposure_values: dict[str, list[float]] | None = None) -> None:
        self._exposure_values = exposure_values

    def get_exposure(
        self, timestamps: pl.Series, symbols: list[str]
    ) -> pl.DataFrame:
        from datetime import timezone

        # Re-emit the panel ts but with UTC tz attached
        ts_naive = timestamps.cast(pl.Datetime).to_list()
        ts_aware = [t.replace(tzinfo=timezone.utc) for t in ts_naive]
        ts_pl = pl.Series("ts", ts_aware)

        data: dict[str, object] = {"ts": ts_pl}
        n = len(ts_aware)
        for sym in symbols:
            if self._exposure_values and sym in self._exposure_values:
                vals = list(self._exposure_values[sym])
                data[sym] = (vals + [0.0] * max(0, n - len(vals)))[:n]
            else:
                data[sym] = [1.0] * n
        return pl.DataFrame(data)


def test_h3_tz_aware_provider_aligns_with_naive_panel() -> None:
    """Provider returning tz-aware ts must still align with naive panel ts.

    Without the H3 normalization fix, dict-key equality between
    naive ``datetime(...)`` and tz-aware ``datetime(..., tz=UTC)`` would fail
    silently, producing an all-NaN OLS output panel.  The fix unifies both
    sides to ``Datetime("ns", time_zone=None)`` and strips tzinfo before
    building the lookup dict.
    """
    rng = np.random.default_rng(13)
    N = len(SYMBOLS)
    exposure_vals = rng.standard_normal(N)
    alpha, beta = 1.0, 2.0

    sym_data: dict[str, list[float]] = {}
    exp_data: dict[str, list[float]] = {}
    for i, sym in enumerate(SYMBOLS):
        y_vals = [alpha + beta * exposure_vals[i]] * T
        sym_data[sym] = y_vals
        exp_data[sym] = [exposure_vals[i]] * T

    panel_data: dict[str, object] = {"ts": TIMESTAMPS}
    panel_data.update(sym_data)
    panel = pl.DataFrame(panel_data).with_columns(pl.col("ts").cast(pl.Datetime))

    tz_provider = _TzAwareExposure(exposure_values=exp_data)
    uni = _make_universe()
    aligner = Aligner(uni, neutralize=[tz_provider])

    # Must NOT raise PITViolationError — H3 fix normalizes both sides
    out = aligner.align(panel)

    # Residuals must be near-zero (exact linear combo, no noise) — proving
    # the OLS regression actually ran with valid exposure values, not all-NaN.
    for sym in SYMBOLS:
        arr = out[sym].to_numpy()
        valid = arr[~np.isnan(arr)]
        assert len(valid) > 0, f"{sym}: expected non-NaN residuals after tz alignment"
        mean_abs = float(np.abs(valid).mean())
        assert mean_abs < 1e-8, (
            f"{sym}: tz-aware exposure should align after normalization, "
            f"got mean_abs={mean_abs:.2e} (suggests OLS ran on NaN-only matrix)"
        )


class _MisalignedTsExposure:
    """Test ExposureProvider that returns timestamps unrelated to the panel.

    Used to exercise H3's defensive guard: when no ts overlap exists, the
    aligner must raise PITViolationError instead of silently producing an
    all-NaN OLS panel.
    """

    name = "misaligned_fake"

    def get_exposure(
        self, timestamps: pl.Series, symbols: list[str]
    ) -> pl.DataFrame:
        # Return ts that are within the panel's max but are NOT actual panel rows
        # — every minute past midnight, which won't match the daily panel rows.
        n = len(timestamps)
        bogus_ts = [datetime(2026, 1, 1, 12, m) for m in range(n)]
        data: dict[str, object] = {"ts": bogus_ts}
        for sym in symbols:
            data[sym] = [1.0] * n
        return pl.DataFrame(data).with_columns(pl.col("ts").cast(pl.Datetime))


def test_h3_full_ts_mismatch_raises_pit_violation() -> None:
    """When provider ts has zero overlap with panel ts, raise PITViolationError.

    Previous behaviour (pre-H3 fix): silently produce an all-NaN panel with
    no log warning, masking the misalignment bug from operators.
    """
    uni = _make_universe()
    panel = _make_panel()
    bad_provider = _MisalignedTsExposure()
    aligner = Aligner(uni, neutralize=[bad_provider])
    with pytest.raises(PITViolationError, match="not aligned"):
        aligner.align(panel)
