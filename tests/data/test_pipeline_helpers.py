"""NT/asyncio/pandas-free tests for ``tinohelm.data.pipeline_helpers``.

These cover the pure helpers extracted from ``pipeline.py`` so the
category-resolution, progress-math, date-boundary, Vision-stem-parsing and
CSV-header-sniffing logic can be exercised under a lean CI image without the
``nautilus_trader`` wheel.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone

import pytest

from tinohelm.data.pipeline_helpers import (
    CANONICAL_WRITE_CATEGORIES,
    DOWNLOAD_PROGRESS_BASE,
    DOWNLOAD_PROGRESS_SPAN,
    INTERVAL_CONVENTION,
    WRITE_CATEGORY,
    compute_chunk_subprogress,
    compute_stage_pct,
    csv_has_header,
    date_end_dt,
    date_end_ns,
    date_start_dt,
    date_start_ns,
    parse_vision_coverage_end,
    resolve_db_category,
    resolve_db_interval,
    resolve_write_category,
)


# ---------------------------------------------------------------------------
# 1. Module isolation — proves zero NT/pandas/sqlalchemy/httpx dependency
# ---------------------------------------------------------------------------

class TestModuleIsolation:
    """Importing the helpers must NOT pull in heavy frameworks."""

    def test_pure_module_imports(self):
        # Sanity: helper module is importable in this process
        import tinohelm.data.pipeline_helpers as mod
        assert mod.WRITE_CATEGORY is WRITE_CATEGORY

    def test_no_heavy_imports_after_loading(self):
        # The helper module itself only depends on the stdlib. It must not
        # cause ``pandas``/``sqlalchemy``/``httpx``/``nautilus_trader`` to be
        # imported transitively.
        # We can't easily restart the interpreter, so we verify the module
        # source has no import statements for the heavy modules.
        import tinohelm.data.pipeline_helpers as mod
        src_path = mod.__file__
        with open(src_path, "r", encoding="utf-8") as f:
            content = f.read()
        for forbidden in (
            "import pandas",
            "import sqlalchemy",
            "import httpx",
            "import nautilus_trader",
            "from pandas",
            "from sqlalchemy",
            "from httpx",
            "from nautilus_trader",
        ):
            assert forbidden not in content, (
                f"pipeline_helpers must not contain '{forbidden}' "
                "(would break NT-free unit-test layer)"
            )


# ---------------------------------------------------------------------------
# 2. Canonical mappings — pin contents (regression guard for silent edits)
# ---------------------------------------------------------------------------

class TestCanonicalMappings:
    def test_write_category_is_immutable(self):
        with pytest.raises(TypeError):
            WRITE_CATEGORY["new_type"] = "bar"  # type: ignore[index]

    def test_interval_convention_is_immutable(self):
        with pytest.raises(TypeError):
            INTERVAL_CONVENTION["new_type"] = "1m"  # type: ignore[index]

    def test_write_category_canonical_keys(self):
        assert WRITE_CATEGORY["klines"] == "bar"
        assert WRITE_CATEGORY["markPriceKlines"] == "mark_price"
        assert WRITE_CATEGORY["indexPriceKlines"] == "index_price"
        assert "premiumIndexKlines" not in WRITE_CATEGORY
        assert WRITE_CATEGORY["trades"] == "trade_tick"
        assert WRITE_CATEGORY["bookTicker"] == "quote_tick"
        assert WRITE_CATEGORY["fundingRate"] == "funding_rate"
        assert "bookDepth" not in WRITE_CATEGORY
        assert "liquidationSnapshot" not in WRITE_CATEGORY
        assert "metrics" not in WRITE_CATEGORY
        assert len(WRITE_CATEGORY) == 6

    def test_write_categories_are_idempotent_but_not_source_type_keys(self):
        assert CANONICAL_WRITE_CATEGORIES == frozenset({
            "bar", "trade_tick", "quote_tick", "funding_rate",
            "mark_price", "index_price",
        })
        for category in CANONICAL_WRITE_CATEGORIES:
            assert resolve_write_category(category) == category
            assert resolve_db_category(category) == category
        # WRITE_CATEGORY remains a source_type map used by resolve_catalog_path;
        # canonical categories must not create synthetic roots like ticks/trade_tick.
        assert "trade_tick" not in WRITE_CATEGORY

    def test_interval_convention_canonical_keys(self):
        assert INTERVAL_CONVENTION["trades"] == "tick"
        assert INTERVAL_CONVENTION["bookTicker"] == "tick"
        assert INTERVAL_CONVENTION["fundingRate"] == "8h"
        assert "bookDepth" not in INTERVAL_CONVENTION
        assert "metrics" not in INTERVAL_CONVENTION

    def test_progress_band_constants(self):
        assert DOWNLOAD_PROGRESS_BASE == 5
        assert DOWNLOAD_PROGRESS_SPAN == 90
        assert DOWNLOAD_PROGRESS_BASE + DOWNLOAD_PROGRESS_SPAN == 95


# ---------------------------------------------------------------------------
# 3. resolve_write_category — fallback "custom"
# ---------------------------------------------------------------------------

class TestResolveWriteCategory:
    def test_known_klines(self):
        assert resolve_write_category("klines") == "bar"

    def test_known_trades(self):
        assert resolve_write_category("trades") == "trade_tick"

    def test_known_funding_rate(self):
        assert resolve_write_category("fundingRate") == "funding_rate"

    def test_known_mark_price(self):
        assert resolve_write_category("markPriceKlines") == "mark_price"

    def test_known_index_price(self):
        assert resolve_write_category("indexPriceKlines") == "index_price"

    def test_unknown_returns_custom(self):
        assert resolve_write_category("anUnknownType") == "custom"

    def test_empty_string_returns_custom(self):
        assert resolve_write_category("") == "custom"


# ---------------------------------------------------------------------------
# 4. resolve_db_category — fallback to input
# ---------------------------------------------------------------------------

class TestResolveDbCategory:
    def test_known_klines(self):
        assert resolve_db_category("klines") == "bar"

    def test_known_book_ticker(self):
        assert resolve_db_category("bookTicker") == "quote_tick"

    def test_known_mark_price(self):
        assert resolve_db_category("markPriceKlines") == "mark_price"

    def test_known_index_price(self):
        assert resolve_db_category("indexPriceKlines") == "index_price"

    def test_unknown_returns_input(self):
        # This is the contract: keep the row discoverable even if the type
        # isn't yet a first-class category.
        assert resolve_db_category("future_type") == "future_type"

    def test_empty_string_returns_empty(self):
        assert resolve_db_category("") == ""

    def test_distinct_from_resolve_write_category(self):
        # The two helpers MUST disagree on unknowns — their fallback
        # semantics are deliberately different (the bug-prone case that
        # motivated the extraction).
        unknown = "exoticType"
        assert resolve_write_category(unknown) == "custom"
        assert resolve_db_category(unknown) == "exoticType"


# ---------------------------------------------------------------------------
# 5. resolve_db_interval — three-tier precedence
# ---------------------------------------------------------------------------

class TestResolveDbInterval:
    def test_explicit_interval_wins(self):
        assert resolve_db_interval("klines", "5m") == "5m"

    def test_explicit_interval_wins_for_intervalless_type(self):
        # Even for "trades" (which has a "tick" convention), an explicit
        # interval still wins.
        assert resolve_db_interval("trades", "1m") == "1m"

    def test_falls_back_to_convention_for_funding_rate(self):
        assert resolve_db_interval("fundingRate", None) == "8h"

    def test_falls_back_to_convention_for_trades(self):
        assert resolve_db_interval("trades", None) == "tick"

    def test_falls_back_to_tick_for_unknown(self):
        assert resolve_db_interval("unknownType", None) == "tick"

    def test_empty_string_treated_as_missing(self):
        # Empty string is treated as "not provided" — falls through to
        # convention/default.
        assert resolve_db_interval("fundingRate", "") == "8h"
        assert resolve_db_interval("trades", "") == "tick"

    def test_none_for_klines_falls_back_to_tick(self):
        # klines is not in INTERVAL_CONVENTION (real klines paths always
        # supply an interval) — so a missing interval here yields "tick".
        # Documents the boundary, even though the calling code asserts it
        # never happens.
        assert resolve_db_interval("klines", None) == "tick"




# ---------------------------------------------------------------------------
# 7. compute_stage_pct — linear progress within a band
# ---------------------------------------------------------------------------

class TestComputeStagePct:
    def test_zero_done_returns_base(self):
        assert compute_stage_pct(0, 10) == 5

    def test_total_zero_returns_base(self):
        assert compute_stage_pct(0, 0) == 5

    def test_negative_total_returns_base(self):
        assert compute_stage_pct(5, -1) == 5

    def test_full_completion_returns_top_of_band(self):
        assert compute_stage_pct(10, 10) == 95  # 5 + 90

    def test_half_completion(self):
        # 5 + round(90 * 0.5) = 5 + 45 = 50
        assert compute_stage_pct(1, 2) == 50

    def test_one_third_completion(self):
        # 5 + round(90 / 3) = 5 + 30 = 35
        assert compute_stage_pct(1, 3) == 35

    def test_quarter_completion(self):
        # 5 + round(90 * 0.25) = 5 + round(22.5) = 5 + 22 = 27
        assert compute_stage_pct(1, 4) == 27

    def test_negative_done_clamps_to_zero(self):
        assert compute_stage_pct(-5, 10) == 5

    def test_done_exceeds_total_clamps_to_total(self):
        assert compute_stage_pct(15, 10) == 95

    def test_custom_band(self):
        assert compute_stage_pct(2, 4, base=78, span=12) == 84  # 78 + 6
        assert compute_stage_pct(0, 4, base=78, span=12) == 78
        assert compute_stage_pct(4, 4, base=78, span=12) == 90

    def test_returns_int(self):
        assert isinstance(compute_stage_pct(3, 7), int)


# ---------------------------------------------------------------------------
# 8. compute_chunk_subprogress — interpolates between stage slices
# ---------------------------------------------------------------------------

class TestComputeChunkSubprogress:
    def test_strictly_below_next_slice(self):
        # 0/4 done → base = 5; next = 27
        result = compute_chunk_subprogress(0, 4, 1)
        assert 5 < result < 27

    def test_more_chunks_get_closer_to_next_but_never_reach(self):
        next_pct = compute_stage_pct(1, 4)  # 27
        result = compute_chunk_subprogress(0, 4, 100)
        assert result == next_pct - 1

    def test_at_least_one_chunk_assumption(self):
        with_zero = compute_chunk_subprogress(0, 4, 0)
        with_one = compute_chunk_subprogress(0, 4, 1)
        assert with_zero == with_one

    def test_full_completion_no_room_to_interpolate(self):
        result = compute_chunk_subprogress(4, 4, 10)
        assert result == 95  # base of last slice == top of band

    def test_total_zero_returns_base(self):
        assert compute_chunk_subprogress(0, 0, 5) == 5

    def test_monotonic_in_chunks(self):
        results = [compute_chunk_subprogress(1, 5, c) for c in (1, 2, 5, 10, 50)]
        assert results == sorted(results)

    def test_interpolation_formula_pin(self):
        # 0/2 done → base = 5; next = 50; sub-width = 45
        assert compute_chunk_subprogress(0, 2, 1) == 20
        assert compute_chunk_subprogress(0, 2, 2) == 27
        assert compute_chunk_subprogress(0, 2, 4) == 35


# ---------------------------------------------------------------------------
# 9. UTC date-boundary helpers
# ---------------------------------------------------------------------------

class TestDateBoundaryHelpers:
    def test_date_start_dt_at_midnight_utc(self):
        d = date(2025, 3, 15)
        result = date_start_dt(d)
        assert result == datetime(2025, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert result.tzinfo == timezone.utc

    def test_date_end_dt_is_next_day_midnight(self):
        d = date(2025, 3, 15)
        result = date_end_dt(d)
        assert result == datetime(2025, 3, 16, 0, 0, 0, tzinfo=timezone.utc)

    def test_date_end_dt_handles_month_boundary(self):
        d = date(2025, 1, 31)
        result = date_end_dt(d)
        assert result == datetime(2025, 2, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_date_end_dt_handles_year_boundary(self):
        d = date(2025, 12, 31)
        result = date_end_dt(d)
        assert result == datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_date_start_ns_known_epoch(self):
        # 2025-01-01 UTC → 1735689600 seconds → * 1e9 ns
        d = date(2025, 1, 1)
        assert date_start_ns(d) == 1_735_689_600 * 1_000_000_000

    def test_date_end_ns_is_one_day_after_start_ns(self):
        d = date(2025, 3, 15)
        delta = date_end_ns(d) - date_start_ns(d)
        assert delta == 86_400 * 1_000_000_000

    def test_date_start_ns_returns_int(self):
        assert isinstance(date_start_ns(date(2025, 1, 1)), int)

    def test_leap_day_supported(self):
        d = date(2024, 2, 29)
        assert date_end_dt(d).date() == date(2024, 3, 1)


# ---------------------------------------------------------------------------
# 10. parse_vision_coverage_end — Vision filename stem parsing
# ---------------------------------------------------------------------------

class TestParseVisionCoverageEnd:
    def test_daily_trades_stem(self):
        assert parse_vision_coverage_end(
            "daily", "BTCUSDT-trades-2025-03-15"
        ) == date(2025, 3, 15)

    def test_daily_klines_stem_includes_interval(self):
        # The interval token comes between the data type and the date — the
        # parser uses the LAST 3 hyphen-separated tokens, so the interval
        # doesn't interfere.
        assert parse_vision_coverage_end(
            "daily", "BTCUSDT-klines-1m-2025-03-15"
        ) == date(2025, 3, 15)

    def test_daily_invalid_date_returns_none(self):
        assert parse_vision_coverage_end(
            "daily", "BTCUSDT-trades-2025-13-99"
        ) is None

    def test_daily_too_few_parts_returns_none(self):
        assert parse_vision_coverage_end("daily", "x-y") is None

    def test_monthly_klines_returns_last_day(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-klines-1m-2025-03"
        ) == date(2025, 3, 31)

    def test_monthly_february_non_leap(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-klines-1m-2025-02"
        ) == date(2025, 2, 28)

    def test_monthly_february_leap(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-klines-1m-2024-02"
        ) == date(2024, 2, 29)

    def test_monthly_december_returns_dec_31(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-klines-1m-2024-12"
        ) == date(2024, 12, 31)

    def test_monthly_january(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-klines-1m-2025-01"
        ) == date(2025, 1, 31)

    def test_monthly_invalid_month_returns_none(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-klines-1m-2025-13"
        ) is None

    def test_monthly_zero_month_returns_none(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-klines-1m-2025-00"
        ) is None

    def test_monthly_non_numeric_returns_none(self):
        assert parse_vision_coverage_end(
            "monthly", "BTCUSDT-trades-foo-bar"
        ) is None

    def test_monthly_too_few_parts_returns_none(self):
        assert parse_vision_coverage_end("monthly", "x") is None

    def test_unknown_granularity_returns_none(self):
        assert parse_vision_coverage_end(
            "hourly", "BTCUSDT-klines-1m-2025-03-15"
        ) is None

    def test_empty_stem_returns_none(self):
        assert parse_vision_coverage_end("daily", "") is None
        assert parse_vision_coverage_end("monthly", "") is None

    def test_empty_granularity_returns_none(self):
        assert parse_vision_coverage_end("", "BTCUSDT-trades-2025-03-15") is None


# ---------------------------------------------------------------------------
# 11. csv_has_header
# ---------------------------------------------------------------------------

class TestCsvHasHeader:
    def test_text_header_detected(self):
        assert csv_has_header("open_time,open,high,low,close\n") is True

    def test_digit_first_treated_as_data(self):
        assert csv_has_header("1700000000000,42000.0,42100.0\n") is False

    def test_negative_number_treated_as_data(self):
        # Leading "-" is not a digit, but it IS a sign: this falls through
        # to "looks like a header" by the current rule. Document the
        # boundary so any future change is intentional.
        assert csv_has_header("-1.5,2,3") is True

    def test_empty_string_no_header(self):
        assert csv_has_header("") is False

    def test_whitespace_only_no_header(self):
        assert csv_has_header("   \n") is False

    def test_tab_separator_with_header(self):
        assert csv_has_header("symbol\tprice\tqty") is True

    def test_alpha_column_first(self):
        assert csv_has_header("a,1,2") is True


# ---------------------------------------------------------------------------
# 12. Cross-reference: helpers consumed by pipeline.py with same semantics
# ---------------------------------------------------------------------------

