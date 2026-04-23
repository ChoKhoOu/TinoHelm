"""Unit tests — ``tinohelm.factor.evaluation.rating``.

Locks the 2 rating functions (``compute_rating`` + ``rating_letter``).

Critical pin points:
* ``compute_rating`` thresholds use STRICT ``>``:
    - ``abs(ir) > 1.0`` AND ``pct > 0.60``          → 3  (strong)
    - ``abs(ir) > 0.5`` AND ``pct > 0.55``          → 2  (usable)
    - ``abs(ir) > 0.2``  (no pct gate at this tier) → 1  (weak)
    - else                                           → 0  (invalid)
* ``abs(ir)`` is used so a strongly NEGATIVE IR still rates as strong.
* At tier 1 there is NO pct gate — ``ir=0.3, pct=0.0`` still rates 1.
* ``rating_letter`` uses ``>=3`` for S, ``==2`` for A, ``==1`` for B,
  then 0 + ``pct >= 0.45`` for C else D.  ``pct == 0.45`` ties to C.
* Missing summary / missing keys default to 0 (→ D).

All cases deterministic, < 10 ms.
"""
from __future__ import annotations

import pytest

from tinohelm.factor.evaluation.rating import compute_rating, rating_letter


# ──────────────────────────────────────────────────────────────────────
# compute_rating — threshold matrix
# ──────────────────────────────────────────────────────────────────────


class TestComputeRatingThresholds:
    @pytest.mark.parametrize("ir,pct,expected", [
        # Tier 3 (strong)
        (1.5, 0.65, 3),
        (-1.5, 0.65, 3),          # negative IR still → 3 via abs
        (1.0001, 0.6001, 3),      # just above both thresholds
        # Tier 3 boundaries — strict >
        (1.0, 0.65, 2),           # ir exactly 1.0 → fails strict > 1.0 → falls to tier 2
        (1.5, 0.60, 2),           # pct exactly 0.60 → fails strict > 0.60 → falls to tier 2
        # Tier 2 (usable)
        (0.7, 0.58, 2),
        (-0.7, 0.58, 2),
        (0.5001, 0.5501, 2),      # just above
        # Tier 2 boundaries
        (0.5, 0.58, 1),           # ir exactly 0.5 → tier 1 (ir > 0.2)
        (0.7, 0.55, 1),           # pct exactly 0.55 → tier 1
        # Tier 1 (weak) — no pct gate!
        (0.3, 0.50, 1),
        (0.3, 0.00, 1),           # tier 1 has no pct gate
        (-0.3, 0.00, 1),
        (0.2001, 0.0, 1),         # just above
        # Tier 1 boundary
        (0.2, 0.5, 0),            # ir exactly 0.2 → tier 0
        # Tier 0 (invalid)
        (0.1, 0.50, 0),
        (0.0, 0.5, 0),
    ])
    def test_threshold_matrix(self, ir, pct, expected):
        summary = {"ir": ir, "ic_positive_pct": pct}
        assert compute_rating(summary) == expected

    def test_missing_keys_default_to_zero(self):
        assert compute_rating({}) == 0

    def test_only_ir_present_can_still_earn_tier_1(self):
        # ir > 0.2 alone → tier 1; missing pct is treated as 0 but tier 1 has no pct gate.
        assert compute_rating({"ir": 0.5}) == 1

    def test_only_pct_present_is_rated_zero(self):
        # ir defaults to 0 → abs(0) = 0 → fails every tier.
        assert compute_rating({"ic_positive_pct": 0.99}) == 0


# ──────────────────────────────────────────────────────────────────────
# rating_letter — numeric → letter mapping
# ──────────────────────────────────────────────────────────────────────


class TestRatingLetterTiers:
    def test_strong_tier_maps_to_S(self):
        assert rating_letter(3) == "S"

    def test_rating_above_3_still_S(self):
        # >=3 branch: any integer ≥ 3 → S.
        assert rating_letter(4) == "S"
        assert rating_letter(10) == "S"

    def test_usable_tier_maps_to_A(self):
        assert rating_letter(2) == "A"

    def test_weak_tier_maps_to_B(self):
        assert rating_letter(1) == "B"

    @pytest.mark.parametrize("pct,expected_letter", [
        (0.50, "C"),   # clearly above 0.45
        (0.45, "C"),   # exactly at threshold → strict `>=` → C
        (0.4499, "D"),
        (0.0, "D"),
        (1.0, "C"),
    ])
    def test_invalid_tier_splits_on_pct(self, pct, expected_letter):
        assert rating_letter(0, {"ic_positive_pct": pct}) == expected_letter

    def test_invalid_tier_without_summary_defaults_to_D(self):
        # None → pct default is 0 → 0 < 0.45 → D.
        assert rating_letter(0) == "D"

    def test_invalid_tier_with_empty_summary_defaults_to_D(self):
        assert rating_letter(0, {}) == "D"


# ──────────────────────────────────────────────────────────────────────
# Cross-function integration
# ──────────────────────────────────────────────────────────────────────


class TestIntegratedRating:
    def test_strong_summary_round_trips_to_S(self):
        summary = {"ir": 1.3, "ic_positive_pct": 0.65}
        rating = compute_rating(summary)
        assert rating == 3
        assert rating_letter(rating, summary) == "S"

    def test_usable_summary_round_trips_to_A(self):
        summary = {"ir": 0.7, "ic_positive_pct": 0.58}
        rating = compute_rating(summary)
        assert rating == 2
        assert rating_letter(rating, summary) == "A"

    def test_weak_round_trips_to_B(self):
        summary = {"ir": 0.3, "ic_positive_pct": 0.52}
        rating = compute_rating(summary)
        assert rating == 1
        assert rating_letter(rating, summary) == "B"

    def test_marginal_invalid_round_trips_to_C(self):
        summary = {"ir": 0.05, "ic_positive_pct": 0.50}
        rating = compute_rating(summary)
        assert rating == 0
        assert rating_letter(rating, summary) == "C"

    def test_zero_signal_round_trips_to_D(self):
        summary = {"ir": 0.0, "ic_positive_pct": 0.30}
        rating = compute_rating(summary)
        assert rating == 0
        assert rating_letter(rating, summary) == "D"
