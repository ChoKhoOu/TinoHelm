"""Cross-section neutralization tests — AC-6.2.1.

Tests Aligner's OLS residual computation under various scenarios:
- Single exposure provider (known exact linear)
- Two providers combined OLS
- Constant exposure (singular OLS — should not crash)
- All-NaN exposure absence (PIT violation detection via future ts)
- Universe mask zeroes unlisted symbols
- String provider resolves to builtin
- Unknown string provider raises KeyError
- Residual preserves panel shape
- No-op alignment (empty neutralize) returns PIT-masked panel
- Partial-row universe masking leaves later rows intact
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np
import polars as pl
import pytest

from tinohelm.aligner.aligner import Aligner, PITViolationError
from tinohelm.aligner.exposure import BTCBetaExposure
from tinohelm.aligner import registry as _registry_module
from tinohelm.factor.universe import Universe


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2026, 1, 1)
T = 20
N = 5
SYMBOLS = [f"S{i:02d}" for i in range(N)]
TIMESTAMPS = [datetime(2026, 1, i + 1) for i in range(T)]


def _make_universe(
    listing_override: dict[str, datetime] | None = None,
    delisting_override: dict[str, datetime | None] | None = None,
) -> Universe:
    from tinohelm.factor.universe import _UniverseRow  # noqa: PLC0415

    listing_override = listing_override or {}
    delisting_override = delisting_override or {}
    rows = []
    for sym in SYMBOLS:
        listing = listing_override.get(sym, datetime(1970, 1, 1))
        delisting = delisting_override.get(sym, None)
        rows.append(_UniverseRow(sym, listing, delisting))
    return Universe(rows, name="neut_test")


def _make_panel(seed: int = 42) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    data: dict[str, Any] = {"ts": TIMESTAMPS}
    for sym in SYMBOLS:
        data[sym] = rng.standard_normal(T).tolist()
    return pl.DataFrame(data).with_columns(pl.col("ts").cast(pl.Datetime))


class ControlledExposure:
    """Test ExposureProvider returning fixed per-symbol constant exposures."""

    def __init__(
        self,
        name: str = "ctrl",
        exposure_values: dict[str, float] | None = None,
        future_offset_years: int = 0,
    ) -> None:
        self.name = name
        self._vals = exposure_values or {sym: 1.0 for sym in SYMBOLS}
        self._future_offset = future_offset_years

    def get_exposure(
        self,
        timestamps: pl.Series,
        symbols: list[str],
    ) -> pl.DataFrame:
        ts_list: list[datetime] = timestamps.to_list()
        if self._future_offset > 0:
            max_ts = max(ts_list)
            ts_list = ts_list + [
                datetime(max_ts.year + self._future_offset, max_ts.month, max_ts.day)
            ]
        data: dict[str, Any] = {"ts": ts_list}
        n = len(ts_list)
        for sym in symbols:
            v = self._vals.get(sym, 0.0)
            data[sym] = [v] * n
        return pl.DataFrame(data).with_columns(pl.col("ts").cast(pl.Datetime))


# ---------------------------------------------------------------------------
# Test 1 — single provider, exact linear factor → near-zero residuals
# ---------------------------------------------------------------------------


def test_single_provider_residual_zero() -> None:
    """Residuals ≈ 0 when panel = α + β × exposure (no noise)."""
    rng = np.random.default_rng(0)
    exposure_vals = rng.standard_normal(N)
    alpha, beta = 2.0, 3.0

    sym_data: dict[str, list[float]] = {}
    exp_data: dict[str, float] = {}
    for i, sym in enumerate(SYMBOLS):
        sym_data[sym] = [alpha + beta * exposure_vals[i]] * T
        exp_data[sym] = float(exposure_vals[i])

    panel_dict: dict[str, Any] = {"ts": TIMESTAMPS}
    panel_dict.update(sym_data)
    panel = pl.DataFrame(panel_dict).with_columns(pl.col("ts").cast(pl.Datetime))

    provider = ControlledExposure(exposure_values=exp_data)
    aligner = Aligner(_make_universe(), neutralize=[provider])
    out = aligner.align(panel)

    for sym in SYMBOLS:
        arr = out[sym].to_numpy()
        valid = arr[~np.isnan(arr)]
        mean_abs = float(np.abs(valid).mean()) if len(valid) > 0 else 0.0
        assert mean_abs < 1e-8, f"{sym}: mean |residual| = {mean_abs:.2e}, expected < 1e-8"


# ---------------------------------------------------------------------------
# Test 2 — two providers combined OLS → near-zero residuals
# ---------------------------------------------------------------------------


def test_two_providers_combined_residualization() -> None:
    """Panel = α + β1*exp1 + β2*exp2 → residuals ≈ 0 with both providers."""
    rng = np.random.default_rng(7)
    exp1 = rng.standard_normal(N)
    exp2 = rng.standard_normal(N)
    alpha, beta1, beta2 = 0.5, 1.2, -0.8

    sym_data: dict[str, list[float]] = {}
    exp1_data: dict[str, float] = {}
    exp2_data: dict[str, float] = {}
    for i, sym in enumerate(SYMBOLS):
        sym_data[sym] = [alpha + beta1 * exp1[i] + beta2 * exp2[i]] * T
        exp1_data[sym] = float(exp1[i])
        exp2_data[sym] = float(exp2[i])

    panel_dict: dict[str, Any] = {"ts": TIMESTAMPS}
    panel_dict.update(sym_data)
    panel = pl.DataFrame(panel_dict).with_columns(pl.col("ts").cast(pl.Datetime))

    p1 = ControlledExposure(name="exp1", exposure_values=exp1_data)
    p2 = ControlledExposure(name="exp2", exposure_values=exp2_data)
    aligner = Aligner(_make_universe(), neutralize=[p1, p2])
    out = aligner.align(panel)

    for sym in SYMBOLS:
        arr = out[sym].to_numpy()
        valid = arr[~np.isnan(arr)]
        mean_abs = float(np.abs(valid).mean()) if len(valid) > 0 else 0.0
        assert mean_abs < 1e-8, (
            f"{sym}: two-provider residual mean_abs={mean_abs:.2e}, expected < 1e-8"
        )


# ---------------------------------------------------------------------------
# Test 3 — constant exposure (singular design matrix) — must not crash
# ---------------------------------------------------------------------------


def test_constant_exposure_handles_singular() -> None:
    """Constant exposure across symbols produces singular OLS — must not raise."""
    # All symbols have the same exposure value → X matrix is rank-1 → lstsq handles it
    constant_exp = {sym: 1.0 for sym in SYMBOLS}
    provider = ControlledExposure(name="const", exposure_values=constant_exp)
    panel = _make_panel()
    aligner = Aligner(_make_universe(), neutralize=[provider])
    # Should not raise — lstsq silently handles rank-deficient matrix
    out = aligner.align(panel)
    assert out.shape == panel.shape


# ---------------------------------------------------------------------------
# Test 4 — future timestamp from provider triggers PITViolationError
# ---------------------------------------------------------------------------


def test_future_exposure_ts_raises_pit_violation() -> None:
    """Provider returning a future timestamp triggers PITViolationError."""
    provider = ControlledExposure(name="bad", future_offset_years=1)
    panel = _make_panel()
    aligner = Aligner(_make_universe(), neutralize=[provider])
    with pytest.raises(PITViolationError):
        aligner.align(panel)


# ---------------------------------------------------------------------------
# Test 5 — universe mask zeros out unlisted symbols
# ---------------------------------------------------------------------------


def test_universe_mask_zeros_unlisted() -> None:
    """Symbol listed after all panel timestamps is fully null in output."""
    # S00 lists 2026-02-01 — all panel timestamps are in Jan 2026 → eligible_from ≈ 2026-02-08
    listing_date = datetime(2026, 2, 1)
    uni = _make_universe(listing_override={"S00": listing_date})
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[])
    out = aligner.align(panel)

    s00_col = out["S00"]
    assert s00_col.null_count() == T, (
        f"Expected all {T} rows of S00 to be null (pre-listing); "
        f"got null_count={s00_col.null_count()}"
    )
    # Other symbols should be unaffected
    for sym in SYMBOLS[1:]:
        assert out[sym].null_count() == 0, f"{sym} should have no nulls"


# ---------------------------------------------------------------------------
# Test 6 — string 'btc_beta' resolves to BTCBetaExposure builtin
# ---------------------------------------------------------------------------


def test_string_provider_resolves_to_builtin() -> None:
    """String 'btc_beta' resolves to BTCBetaExposure instance at construction."""
    aligner = Aligner(_make_universe(), neutralize=["btc_beta"])
    assert len(aligner._providers) == 1
    assert isinstance(aligner._providers[0], BTCBetaExposure)


# ---------------------------------------------------------------------------
# Test 7 — unknown string provider raises KeyError at construction
# ---------------------------------------------------------------------------


def test_unknown_string_provider_raises_keyerror() -> None:
    """Unregistered string name raises KeyError immediately at Aligner init."""
    with pytest.raises(KeyError):
        Aligner(_make_universe(), neutralize=["no_such_provider_xyzzy_99"])


# ---------------------------------------------------------------------------
# Test 8 — residual panel preserves shape (T, N+1 columns)
# ---------------------------------------------------------------------------


def test_residual_preserves_panel_shape() -> None:
    """Output panel has the same column names and row count as the input."""
    panel = _make_panel()
    provider = ControlledExposure()
    aligner = Aligner(_make_universe(), neutralize=[provider])
    out = aligner.align(panel)

    assert out.shape == panel.shape, (
        f"Shape mismatch: input {panel.shape}, output {out.shape}"
    )
    assert out.columns == panel.columns, (
        f"Column mismatch: input {panel.columns}, output {out.columns}"
    )


# ---------------------------------------------------------------------------
# Test 9 — empty neutralize returns PIT-masked panel (no OLS)
# ---------------------------------------------------------------------------


def test_no_neutralize_returns_pit_masked_panel() -> None:
    """With neutralize=[], align() only applies PIT mask — no OLS transform."""
    uni = _make_universe()  # all listing_date=1970 → no masking
    panel = _make_panel()
    aligner = Aligner(uni, neutralize=[])
    out = aligner.align(panel)

    # No masking (all 1970 listing) and no OLS → values unchanged
    for sym in SYMBOLS:
        assert out[sym].null_count() == 0, f"{sym} should have no nulls with 1970 listing"


# ---------------------------------------------------------------------------
# Test 10 — partial-row universe masking: pre-listing rows null, post intact
# ---------------------------------------------------------------------------


def test_partial_row_masking_leaves_later_rows_intact() -> None:
    """Symbol listing mid-panel: pre-eligible rows null, post-eligible rows non-null."""
    # S01 lists 2026-01-01 → eligible_from = 2026-01-08 (+ 7 day grace)
    # Timestamps 0-6 (Jan 1-7) → null; timestamps 7-19 (Jan 8-20) → non-null
    listing_date = datetime(2026, 1, 1)
    uni = _make_universe(listing_override={"S01": listing_date})
    panel = _make_panel(seed=55)
    aligner = Aligner(uni, neutralize=[])
    out = aligner.align(panel)

    s01 = out["S01"]
    assert s01.null_count() == 7, (
        f"Expected 7 null rows for S01 (pre-listing), got {s01.null_count()}"
    )
    assert s01[7:].null_count() == 0, (
        "Expected 0 nulls in rows 7+ for S01 (post-listing)"
    )
