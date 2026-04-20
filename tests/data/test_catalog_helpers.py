"""NT-free unit tests for :mod:`tinohelm.data.catalog_helpers`.

Covers:
- Canonical mappings (``INTERVAL_MAP``, ``CATEGORY_DIR``,
  ``WRITABLE_CATEGORIES``) — immutability + content spot-checks.
- Interval parsing (``interval_to_step_unit``,
  ``interval_to_nanoseconds``).
- Catalog path resolution (``resolve_catalog_path``) — delegates to
  ``pipeline_helpers.WRITE_CATEGORY`` and the writable-category allowlist.
- Timestamp helpers (``ns_to_iso``, ``count_duplicates``, ``find_gaps``).
- OHLCV integrity (``is_ohlc_valid``, ``compute_change_pct``,
  ``detect_price_jumps``).
- Validation-report assembly (``classify_status``,
  ``build_validation_issues``).
- Bar merging (``dedupe_by_ts``, ``merge_bars``).

Also includes a backward-compatibility test class that pins the alias
identities in ``tinohelm.data.catalog`` to the helpers module — so a
future rename in helpers cannot silently leave a stale local copy in
``catalog.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from tinohelm.data import catalog
from tinohelm.data.catalog_helpers import (
    CATEGORY_DIR,
    INTERVAL_MAP,
    WRITABLE_CATEGORIES,
    build_validation_issues,
    classify_status,
    compute_change_pct,
    count_duplicates,
    dedupe_by_ts,
    detect_price_jumps,
    find_gaps,
    interval_to_nanoseconds,
    interval_to_step_unit,
    is_ohlc_valid,
    merge_bars,
    ns_to_iso,
    resolve_catalog_path,
)
from tinohelm.data.pipeline_helpers import WRITE_CATEGORY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class _FakeBar:
    """Stand-in for NT's ``Bar`` — only needs ``ts_event`` and a tag."""
    ts_event: int
    tag: str = ""


# ---------------------------------------------------------------------------
# 1. Canonical mappings
# ---------------------------------------------------------------------------

class TestIntervalMap:
    def test_is_immutable(self):
        assert isinstance(INTERVAL_MAP, MappingProxyType)
        with pytest.raises(TypeError):
            INTERVAL_MAP["2d"] = (2, "DAY")  # type: ignore[index]

    def test_sample_entries(self):
        assert INTERVAL_MAP["1m"] == (1, "MINUTE")
        assert INTERVAL_MAP["5m"] == (5, "MINUTE")
        assert INTERVAL_MAP["1h"] == (1, "HOUR")
        assert INTERVAL_MAP["12h"] == (12, "HOUR")
        assert INTERVAL_MAP["1d"] == (1, "DAY")

    def test_all_aggregations_known(self):
        for token, (step, agg) in INTERVAL_MAP.items():
            assert step > 0, token
            assert agg in {"MINUTE", "HOUR", "DAY"}, (token, agg)


class TestCategoryDir:
    def test_bar_and_ticks(self):
        assert CATEGORY_DIR["bar"] == "bar"
        assert CATEGORY_DIR["trade_tick"] == "ticks"

    def test_is_immutable(self):
        assert isinstance(CATEGORY_DIR, MappingProxyType)
        with pytest.raises(TypeError):
            CATEGORY_DIR["bar"] = "other"  # type: ignore[index]

    def test_writable_categories_derived(self):
        assert WRITABLE_CATEGORIES == frozenset(CATEGORY_DIR.keys())
        assert isinstance(WRITABLE_CATEGORIES, frozenset)


# ---------------------------------------------------------------------------
# 2. Interval parsing
# ---------------------------------------------------------------------------

class TestIntervalToStepUnit:
    @pytest.mark.parametrize("tok,expected", [
        ("1m", (1, "MINUTE")),
        ("3m", (3, "MINUTE")),
        ("5m", (5, "MINUTE")),
        ("15m", (15, "MINUTE")),
        ("30m", (30, "MINUTE")),
        ("1h", (1, "HOUR")),
        ("2h", (2, "HOUR")),
        ("4h", (4, "HOUR")),
        ("6h", (6, "HOUR")),
        ("8h", (8, "HOUR")),
        ("12h", (12, "HOUR")),
        ("1d", (1, "DAY")),
    ])
    def test_all_supported_tokens(self, tok, expected):
        assert interval_to_step_unit(tok) == expected

    def test_unknown_raises_with_supported_list(self):
        with pytest.raises(ValueError) as exc_info:
            interval_to_step_unit("7h")
        msg = str(exc_info.value)
        assert "7h" in msg
        # Error message must list supported tokens so the CLI/UI can surface
        # them without keeping a second copy of the list.
        assert "1m" in msg and "1d" in msg

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            interval_to_step_unit("")

    def test_case_sensitive(self):
        # Tokens are lowercase; upper-case is unsupported — the caller is
        # expected to normalize before calling.
        with pytest.raises(ValueError):
            interval_to_step_unit("1M")


class TestIntervalToNanoseconds:
    @pytest.mark.parametrize("tok,expected_ns", [
        ("1m", 60 * 1_000_000_000),
        ("5m", 5 * 60 * 1_000_000_000),
        ("15m", 15 * 60 * 1_000_000_000),
        ("1h", 3600 * 1_000_000_000),
        ("4h", 4 * 3600 * 1_000_000_000),
        ("1d", 86_400 * 1_000_000_000),
    ])
    def test_known_intervals(self, tok, expected_ns):
        assert interval_to_nanoseconds(tok) == expected_ns

    def test_unknown_interval_raises(self):
        with pytest.raises(ValueError):
            interval_to_nanoseconds("7h")


# ---------------------------------------------------------------------------
# 3. resolve_catalog_path — writable-category allowlist
# ---------------------------------------------------------------------------

class TestResolveCatalogPath:
    def test_klines_goes_to_bar_category(self):
        p = resolve_catalog_path("/tmp/cat", "klines")
        assert p == Path("/tmp/cat") / "bar" / "klines"

    def test_mark_price_klines_under_bar(self):
        p = resolve_catalog_path("/tmp/cat", "markPriceKlines")
        assert p == Path("/tmp/cat") / "bar" / "markPriceKlines"

    def test_index_price_klines_under_bar(self):
        p = resolve_catalog_path("/tmp/cat", "indexPriceKlines")
        assert p == Path("/tmp/cat") / "bar" / "indexPriceKlines"

    def test_premium_index_klines_under_bar(self):
        p = resolve_catalog_path("/tmp/cat", "premiumIndexKlines")
        assert p == Path("/tmp/cat") / "bar" / "premiumIndexKlines"

    def test_agg_trades_under_ticks(self):
        p = resolve_catalog_path("/tmp/cat", "aggTrades")
        assert p == Path("/tmp/cat") / "ticks" / "aggTrades"

    def test_trades_under_ticks(self):
        p = resolve_catalog_path("/tmp/cat", "trades")
        assert p == Path("/tmp/cat") / "ticks" / "trades"

    def test_none_returns_base(self):
        assert resolve_catalog_path("/tmp/cat", None) == Path("/tmp/cat")

    def test_empty_string_returns_base(self):
        assert resolve_catalog_path("/tmp/cat", "") == Path("/tmp/cat")

    def test_unknown_source_returns_base(self):
        assert resolve_catalog_path("/tmp/cat", "noSuchType") == Path("/tmp/cat")

    def test_funding_rate_returns_base(self):
        # fundingRate maps to "funding_rate" category — not in WRITABLE_CATEGORIES
        # because catalog.py has no funding_rate writer. Fallthrough = base.
        assert WRITE_CATEGORY["fundingRate"] not in WRITABLE_CATEGORIES
        assert resolve_catalog_path("/tmp/cat", "fundingRate") == Path("/tmp/cat")

    def test_book_ticker_returns_base(self):
        # bookTicker maps to "quote_tick" which has no catalog writer yet.
        assert WRITE_CATEGORY["bookTicker"] not in WRITABLE_CATEGORIES
        assert resolve_catalog_path("/tmp/cat", "bookTicker") == Path("/tmp/cat")

    def test_book_depth_returns_base(self):
        assert resolve_catalog_path("/tmp/cat", "bookDepth") == Path("/tmp/cat")

    def test_metrics_returns_base(self):
        assert resolve_catalog_path("/tmp/cat", "metrics") == Path("/tmp/cat")

    def test_accepts_path_input(self):
        p = resolve_catalog_path(Path("/tmp/cat"), "klines")
        assert p == Path("/tmp/cat") / "bar" / "klines"

    def test_accepts_relative_string(self):
        p = resolve_catalog_path("./cat", "klines")
        assert p == Path("./cat") / "bar" / "klines"


# ---------------------------------------------------------------------------
# 4. Timestamp helpers
# ---------------------------------------------------------------------------

class TestNsToIso:
    def test_epoch_zero(self):
        assert ns_to_iso(0) == "1970-01-01T00:00:00+00:00"

    def test_known_timestamp(self):
        # 2025-01-01T00:00:00Z = 1_735_689_600 s
        ns = 1_735_689_600 * 1_000_000_000
        assert ns_to_iso(ns) == "2025-01-01T00:00:00+00:00"

    def test_includes_tz_suffix(self):
        # Must always serialize with UTC offset so downstream code can
        # round-trip through datetime.fromisoformat without ambiguity.
        assert "+00:00" in ns_to_iso(1_700_000_000 * 1_000_000_000)


class TestCountDuplicates:
    def test_empty(self):
        assert count_duplicates([]) == 0

    def test_no_duplicates(self):
        assert count_duplicates([1, 2, 3, 4, 5]) == 0

    def test_all_duplicates(self):
        assert count_duplicates([7, 7, 7, 7]) == 3

    def test_mixed(self):
        assert count_duplicates([1, 2, 2, 3, 3, 3, 4]) == 3

    def test_generator_input(self):
        def gen():
            for i in (1, 1, 2):
                yield i
        assert count_duplicates(gen()) == 1


class TestFindGaps:
    def _step_ns(self, minutes: int) -> int:
        return minutes * 60 * 1_000_000_000

    def test_empty(self):
        assert find_gaps([], self._step_ns(1)) == []

    def test_single_ts(self):
        assert find_gaps([1_000_000_000], self._step_ns(1)) == []

    def test_no_gaps(self):
        step = self._step_ns(5)
        ts = [i * step for i in range(10)]
        assert find_gaps(ts, step) == []

    def test_single_gap(self):
        step = self._step_ns(5)
        # 0, 5m, 10m, then jump to 30m
        ts = [0, step, 2 * step, 6 * step]
        gaps = find_gaps(ts, step)
        assert len(gaps) == 1
        assert gaps[0]["missing_bars"] == 3  # 15m, 20m, 25m
        assert "start" in gaps[0] and "end" in gaps[0]

    def test_tolerance_absorbs_tiny_delays(self):
        step = self._step_ns(1)
        # Gap of 1.4× step is absorbed (default tolerance 1.5×)
        ts = [0, int(step * 1.4), int(step * 1.4) + step]
        assert find_gaps(ts, step) == []

    def test_custom_tolerance(self):
        step = self._step_ns(1)
        ts = [0, int(step * 1.2), int(step * 1.2) + step]
        assert find_gaps(ts, step, tolerance_mult=1.1) != []

    def test_multiple_gaps(self):
        step = self._step_ns(1)
        ts = [0, step, 10 * step, 11 * step, 30 * step]
        gaps = find_gaps(ts, step)
        assert len(gaps) == 2
        assert gaps[0]["missing_bars"] == 8  # 2..9
        assert gaps[1]["missing_bars"] == 18  # 12..29

    def test_step_ns_must_be_positive(self):
        with pytest.raises(ValueError):
            find_gaps([1, 2], 0)
        with pytest.raises(ValueError):
            find_gaps([1, 2], -1)

    def test_gap_endpoints_iso_formatted(self):
        step = self._step_ns(5)
        ts = [0, 10 * step]
        gaps = find_gaps(ts, step)
        assert gaps[0]["start"].startswith("1970-01-01")
        assert gaps[0]["end"].startswith("1970-01-01")


# ---------------------------------------------------------------------------
# 5. OHLCV integrity
# ---------------------------------------------------------------------------

class TestIsOhlcValid:
    def test_valid_bar(self):
        assert is_ohlc_valid(100, 110, 95, 105) is True

    def test_equal_ohlc(self):
        assert is_ohlc_valid(100, 100, 100, 100) is True

    def test_high_below_open_invalid(self):
        assert is_ohlc_valid(100, 95, 90, 92) is False

    def test_high_below_close_invalid(self):
        assert is_ohlc_valid(100, 102, 95, 105) is False

    def test_low_above_open_invalid(self):
        assert is_ohlc_valid(100, 110, 105, 108) is False

    def test_low_above_close_invalid(self):
        assert is_ohlc_valid(100, 110, 102, 98) is False

    def test_high_below_low_invalid(self):
        assert is_ohlc_valid(100, 90, 95, 92) is False

    def test_floating_point_rounding_absorbed(self):
        # high is 1e-12 below open — within default tolerance 1e-10
        assert is_ohlc_valid(100.0, 100.0 - 1e-12, 99.0, 99.5) is True

    def test_tolerance_rejection_beyond_tol(self):
        # high is 1e-9 below open — outside default tolerance
        assert is_ohlc_valid(100.0, 100.0 - 1e-9, 99.0, 99.5) is False

    def test_custom_tolerance(self):
        assert is_ohlc_valid(100.0, 99.5, 99.0, 99.4, tol=1.0) is True


class TestComputeChangePct:
    def test_basic_up_move(self):
        assert compute_change_pct(100.0, 110.0) == pytest.approx(0.10)

    def test_basic_down_move(self):
        assert compute_change_pct(100.0, 90.0) == pytest.approx(0.10)

    def test_no_change(self):
        assert compute_change_pct(100.0, 100.0) == 0.0

    def test_prev_zero_returns_none(self):
        assert compute_change_pct(0.0, 10.0) is None

    def test_prev_negative_returns_none(self):
        assert compute_change_pct(-1.0, 10.0) is None

    def test_prev_none_returns_none(self):
        assert compute_change_pct(None, 10.0) is None  # type: ignore[arg-type]

    def test_abs_value_positive(self):
        # Even for downward moves the result is positive — threshold check uses absolute.
        assert compute_change_pct(200.0, 100.0) == pytest.approx(0.5)


class TestDetectPriceJumps:
    def test_empty(self):
        assert detect_price_jumps([]) == []

    def test_single_bar_no_jumps(self):
        assert detect_price_jumps([(0, 100.0)]) == []

    def test_small_moves_skipped(self):
        bars = [(0, 100.0), (1, 102.0), (2, 101.0)]
        assert detect_price_jumps(bars) == []

    def test_jump_detected(self):
        bars = [(0, 100.0), (1, 115.0)]
        jumps = detect_price_jumps(bars)
        assert len(jumps) == 1
        assert jumps[0]["prev_close"] == 100.0
        assert jumps[0]["current_close"] == 115.0
        assert jumps[0]["change_pct"] == 15.0

    def test_custom_threshold(self):
        bars = [(0, 100.0), (1, 105.0)]
        # Default 10% — 5% is below, no jump
        assert detect_price_jumps(bars, threshold=0.10) == []
        # Custom 3% — 5% is above, jump
        assert len(detect_price_jumps(bars, threshold=0.03)) == 1

    def test_multiple_jumps(self):
        bars = [(0, 100.0), (1, 120.0), (2, 121.0), (3, 80.0)]
        jumps = detect_price_jumps(bars)
        # 100→120 (+20%) and 121→80 (-33.9%); 120→121 is ~0.8%.
        assert len(jumps) == 2
        assert jumps[0]["change_pct"] == 20.0
        # 121→80 = 41/121 = 0.3388…
        assert jumps[1]["change_pct"] == pytest.approx(33.88, rel=0.01)

    def test_prev_zero_skipped(self):
        bars = [(0, 0.0), (1, 100.0)]
        assert detect_price_jumps(bars) == []

    def test_timestamps_in_output_iso_formatted(self):
        bars = [(0, 100.0), (60 * 1_000_000_000, 200.0)]
        jumps = detect_price_jumps(bars)
        assert jumps[0]["timestamp"].startswith("1970-01-01")

    def test_threshold_boundary_strict(self):
        # Exactly threshold — NOT a jump (strict > comparison)
        bars = [(0, 100.0), (1, 110.0)]
        assert detect_price_jumps(bars, threshold=0.10) == []


# ---------------------------------------------------------------------------
# 6. Validation-report assembly
# ---------------------------------------------------------------------------

class TestClassifyStatus:
    def test_errors_dominate(self):
        assert classify_status(has_errors=True, has_warnings=True) == "errors"
        assert classify_status(has_errors=True, has_warnings=False) == "errors"

    def test_warnings_when_no_errors(self):
        assert classify_status(has_errors=False, has_warnings=True) == "warnings"

    def test_ok_when_neither(self):
        assert classify_status(has_errors=False, has_warnings=False) == "ok"


class TestBuildValidationIssues:
    def test_all_zero_returns_empty(self):
        issues = build_validation_issues(
            duplicates=0,
            gaps=[],
            ohlc_violations=0,
            zero_volume_bars=0,
            price_jumps=[],
            jump_threshold=0.10,
        )
        assert issues == []

    def test_duplicates_only(self):
        issues = build_validation_issues(
            duplicates=3,
            gaps=[],
            ohlc_violations=0,
            zero_volume_bars=0,
            price_jumps=[],
            jump_threshold=0.10,
        )
        assert issues == ["Found 3 duplicate timestamp(s)"]

    def test_gaps_summed(self):
        gaps = [
            {"missing_bars": 2},
            {"missing_bars": 5},
            {"missing_bars": 1},
        ]
        issues = build_validation_issues(
            duplicates=0,
            gaps=gaps,
            ohlc_violations=0,
            zero_volume_bars=0,
            price_jumps=[],
            jump_threshold=0.10,
        )
        assert len(issues) == 1
        assert "3 gap(s)" in issues[0]
        assert "~8 missing" in issues[0]

    def test_ohlc_violations(self):
        issues = build_validation_issues(
            duplicates=0,
            gaps=[],
            ohlc_violations=4,
            zero_volume_bars=0,
            price_jumps=[],
            jump_threshold=0.10,
        )
        assert len(issues) == 1
        assert "4 bar(s)" in issues[0]
        assert "invalid OHLC" in issues[0]

    def test_zero_volume(self):
        issues = build_validation_issues(
            duplicates=0,
            gaps=[],
            ohlc_violations=0,
            zero_volume_bars=2,
            price_jumps=[],
            jump_threshold=0.10,
        )
        assert issues == ["Found 2 zero-volume bar(s)"]

    def test_price_jumps_uses_threshold_pct(self):
        issues = build_validation_issues(
            duplicates=0,
            gaps=[],
            ohlc_violations=0,
            zero_volume_bars=0,
            price_jumps=[{"timestamp": "x"}, {"timestamp": "y"}],
            jump_threshold=0.10,
        )
        assert len(issues) == 1
        assert "2 price jump(s)" in issues[0]
        assert "exceeding 10%" in issues[0]

    def test_price_jumps_custom_threshold_rendered(self):
        issues = build_validation_issues(
            duplicates=0,
            gaps=[],
            ohlc_violations=0,
            zero_volume_bars=0,
            price_jumps=[{"timestamp": "x"}],
            jump_threshold=0.25,
        )
        assert "exceeding 25%" in issues[0]

    def test_all_categories_combined(self):
        issues = build_validation_issues(
            duplicates=2,
            gaps=[{"missing_bars": 1}],
            ohlc_violations=1,
            zero_volume_bars=1,
            price_jumps=[{"timestamp": "x"}],
            jump_threshold=0.10,
        )
        assert len(issues) == 5
        # Order is stable: duplicates, gaps, ohlc, zero_volume, price_jumps
        assert "duplicate" in issues[0]
        assert "gap" in issues[1]
        assert "OHLC" in issues[2]
        assert "zero-volume" in issues[3]
        assert "price jump" in issues[4]

    def test_gap_missing_bars_fallback_to_zero(self):
        # Robustness: an entry without "missing_bars" is counted as 0.
        issues = build_validation_issues(
            duplicates=0,
            gaps=[{}],
            ohlc_violations=0,
            zero_volume_bars=0,
            price_jumps=[],
            jump_threshold=0.10,
        )
        assert "~0 missing bar(s)" in issues[0]


# ---------------------------------------------------------------------------
# 7. Bar merging
# ---------------------------------------------------------------------------

class TestDedupeByTs:
    def test_empty(self):
        assert dedupe_by_ts([]) == []

    def test_preserves_single_item(self):
        b = _FakeBar(ts_event=100, tag="a")
        assert dedupe_by_ts([b]) == [b]

    def test_sorts_ascending(self):
        bars = [
            _FakeBar(ts_event=200, tag="c"),
            _FakeBar(ts_event=100, tag="a"),
            _FakeBar(ts_event=150, tag="b"),
        ]
        out = dedupe_by_ts(bars)
        assert [x.ts_event for x in out] == [100, 150, 200]

    def test_keeps_last_on_collision(self):
        bars = [
            _FakeBar(ts_event=100, tag="first"),
            _FakeBar(ts_event=100, tag="second"),
            _FakeBar(ts_event=100, tag="third"),
        ]
        out = dedupe_by_ts(bars)
        assert len(out) == 1
        assert out[0].tag == "third"

    def test_generator_input(self):
        def gen():
            yield _FakeBar(ts_event=200, tag="a")
            yield _FakeBar(ts_event=100, tag="b")
        out = dedupe_by_ts(gen())
        assert [b.ts_event for b in out] == [100, 200]


class TestMergeBars:
    def test_both_empty(self):
        assert merge_bars([], []) == []

    def test_existing_only(self):
        bars = [_FakeBar(ts_event=100), _FakeBar(ts_event=200)]
        out = merge_bars(bars, [])
        assert [b.ts_event for b in out] == [100, 200]

    def test_new_only(self):
        bars = [_FakeBar(ts_event=100), _FakeBar(ts_event=200)]
        out = merge_bars([], bars)
        assert [b.ts_event for b in out] == [100, 200]

    def test_no_collisions(self):
        existing = [_FakeBar(ts_event=100), _FakeBar(ts_event=200)]
        new = [_FakeBar(ts_event=150), _FakeBar(ts_event=250)]
        out = merge_bars(existing, new)
        assert [b.ts_event for b in out] == [100, 150, 200, 250]

    def test_new_wins_on_collision(self):
        existing = [_FakeBar(ts_event=100, tag="old")]
        new = [_FakeBar(ts_event=100, tag="new")]
        out = merge_bars(existing, new)
        assert len(out) == 1
        assert out[0].tag == "new"

    def test_new_wins_even_with_multiple_existing(self):
        # Mix of overlaps and non-overlaps
        existing = [
            _FakeBar(ts_event=100, tag="e-100"),
            _FakeBar(ts_event=200, tag="e-200"),
            _FakeBar(ts_event=300, tag="e-300"),
        ]
        new = [
            _FakeBar(ts_event=200, tag="n-200"),
            _FakeBar(ts_event=250, tag="n-250"),
        ]
        out = merge_bars(existing, new)
        tags = {b.ts_event: b.tag for b in out}
        assert tags == {
            100: "e-100",
            200: "n-200",  # new wins
            250: "n-250",
            300: "e-300",
        }
        assert [b.ts_event for b in out] == [100, 200, 250, 300]


# ---------------------------------------------------------------------------
# 8. Backward-compatibility with ``tinohelm.data.catalog``
# ---------------------------------------------------------------------------

class TestCatalogBackwardCompat:
    """Pin the alias identities so a future rename in helpers can't leave
    a stale local copy living on in ``catalog.py``.
    """
    def test_interval_map_alias_is_same_object(self):
        assert catalog._INTERVAL_MAP is INTERVAL_MAP

    def test_category_dir_alias_is_same_object(self):
        assert catalog._CATEGORY_DIR is CATEGORY_DIR

    def test_source_to_category_is_subset_of_write_category(self):
        for src, cat in catalog._SOURCE_TO_CATEGORY.items():
            assert WRITE_CATEGORY[src] == cat
            assert cat in WRITABLE_CATEGORIES

    def test_source_to_category_excludes_non_writable(self):
        # Any source type whose category is NOT writable by catalog must
        # be absent from the compat map (e.g. fundingRate, bookTicker).
        for src in catalog._SOURCE_TO_CATEGORY:
            assert WRITE_CATEGORY[src] in WRITABLE_CATEGORIES
        assert "fundingRate" not in catalog._SOURCE_TO_CATEGORY
        assert "bookTicker" not in catalog._SOURCE_TO_CATEGORY

    def test_resolve_catalog_path_reexported(self):
        # Public re-export so callers (runner, pipeline, data API) can keep
        # importing from catalog.
        assert catalog.resolve_catalog_path is resolve_catalog_path

    def test_interval_to_nanoseconds_wrapper(self):
        assert catalog._interval_to_nanoseconds("5m") == interval_to_nanoseconds("5m")
        assert catalog._interval_to_nanoseconds("1h") == interval_to_nanoseconds("1h")

    def test_interval_to_nanoseconds_wrapper_rejects_unknown(self):
        # The wrapper now delegates to interval_to_step_unit → ValueError.
        # Previously the raw lookup would KeyError; the new behaviour is
        # strictly more informative. Tests are the lock for this change.
        with pytest.raises(ValueError):
            catalog._interval_to_nanoseconds("7h")
