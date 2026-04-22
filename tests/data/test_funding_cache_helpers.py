"""Tests for tinohelm.data.funding_cache_helpers — NT-free, filesystem-free.

Every function in ``funding_cache_helpers`` is pure. This module should stay
dependency-free so it can ride on the fast inner loop of the test suite.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import pytest

from tinohelm.data.funding_cache_helpers import (
    DEFAULT_FUNDING_INTERVAL_MINUTES,
    compute_fetch_start,
    dedup_and_sort_records,
    ensure_utc,
    filter_records_by_range,
    from_epoch_ms,
    to_epoch_ms,
)


# ---------------------------------------------------------------------------
# Independence: pure module must not drag in NT, nothing network, nothing IO.
# ---------------------------------------------------------------------------

class TestNtFreeIndependence:
    def test_module_source_does_not_reference_nautilus(self):
        import tinohelm.data.funding_cache_helpers as mod

        src = open(mod.__file__).read()
        assert "nautilus_trader" not in src
        assert "from nautilus" not in src

    def test_module_source_does_not_do_io(self):
        # Sanity: if someone sneaks a filesystem/network call into the pure
        # layer, fail the build.
        import tinohelm.data.funding_cache_helpers as mod

        src = open(mod.__file__).read()
        assert "open(" not in src
        assert "requests" not in src
        assert "httpx" not in src

    def test_re_import_does_not_load_nautilus(self):
        pre = "nautilus_trader" in sys.modules
        sys.modules.pop("tinohelm.data.funding_cache_helpers", None)
        import tinohelm.data.funding_cache_helpers  # noqa: F401

        if not pre:
            assert "nautilus_trader" not in sys.modules


# ---------------------------------------------------------------------------
# ensure_utc / to_epoch_ms / from_epoch_ms
# ---------------------------------------------------------------------------

class TestEnsureUtc:
    def test_naive_gets_utc_tzinfo(self):
        out = ensure_utc(datetime(2024, 1, 1, 12, 0, 0))
        assert out.tzinfo == timezone.utc

    def test_aware_utc_passes_through(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert ensure_utc(dt) is dt or ensure_utc(dt) == dt

    def test_aware_non_utc_passes_through_unchanged(self):
        # We do NOT convert — we only back-fill missing tzinfo.
        tokyo = timezone(timedelta(hours=9))
        dt = datetime(2024, 1, 1, 9, 0, 0, tzinfo=tokyo)
        out = ensure_utc(dt)
        assert out.tzinfo == tokyo
        assert out == dt


class TestEpochMsRoundTrip:
    def test_zero_epoch(self):
        assert to_epoch_ms(datetime(1970, 1, 1, tzinfo=timezone.utc)) == 0

    def test_known_timestamp(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # 2024-01-01T00:00:00 UTC = 1_704_067_200_000 ms
        assert to_epoch_ms(dt) == 1_704_067_200_000

    def test_naive_interpreted_as_utc_not_local(self):
        # Critical: naive datetimes must NOT use the machine's local tz.
        naive = datetime(2024, 1, 1)
        aware = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert to_epoch_ms(naive) == to_epoch_ms(aware)

    def test_round_trip_utc(self):
        dt = datetime(2024, 6, 15, 8, 30, 45, tzinfo=timezone.utc)
        ms = to_epoch_ms(dt)
        back = from_epoch_ms(ms)
        assert back == dt

    def test_from_epoch_ms_is_always_utc_aware(self):
        assert from_epoch_ms(0).tzinfo == timezone.utc
        assert from_epoch_ms(1_704_067_200_000).tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# dedup_and_sort_records
# ---------------------------------------------------------------------------

class TestDedupAndSortRecords:
    def test_empty(self):
        assert dedup_and_sort_records([]) == []

    def test_already_sorted_preserved(self):
        records = [
            {"funding_time_ms": 100, "rate": 0.1},
            {"funding_time_ms": 200, "rate": 0.2},
        ]
        assert dedup_and_sort_records(records) == records

    def test_unsorted_gets_sorted_ascending(self):
        records = [
            {"funding_time_ms": 300, "rate": 0.3},
            {"funding_time_ms": 100, "rate": 0.1},
            {"funding_time_ms": 200, "rate": 0.2},
        ]
        out = dedup_and_sort_records(records)
        assert [r["funding_time_ms"] for r in out] == [100, 200, 300]

    def test_dedup_by_funding_time_ms_later_wins(self):
        # Later occurrence of the same ts overrides earlier — mimics an
        # "update" semantic (new fetch overwrites old cache).
        records = [
            {"funding_time_ms": 100, "rate": 0.1, "stale": True},
            {"funding_time_ms": 100, "rate": 0.2, "stale": False},
        ]
        out = dedup_and_sort_records(records)
        assert len(out) == 1
        assert out[0]["rate"] == 0.2
        assert out[0]["stale"] is False

    def test_invalid_records_dropped(self):
        # Bad rows shouldn't crash the flush.
        records = [
            {"funding_time_ms": 100, "rate": 0.1},
            {},                                  # missing ts
            {"funding_time_ms": "not-a-number"},  # wrong type
            {"funding_time_ms": 200, "rate": 0.2},
            None,                                 # not even a dict
            "not a dict",
        ]
        out = dedup_and_sort_records(records)
        assert [r["funding_time_ms"] for r in out] == [100, 200]

    def test_bool_ts_is_rejected(self):
        # Python quirk: True == 1, False == 0, bool is a numeric subclass.
        # We must NOT accept booleans as valid timestamps.
        records = [
            {"funding_time_ms": True, "rate": 0.1},
            {"funding_time_ms": 100, "rate": 0.2},
        ]
        out = dedup_and_sort_records(records)
        assert [r["funding_time_ms"] for r in out] == [100]

    def test_float_ts_normalised_to_int(self):
        # JSON round-tripping can turn ints into floats if the file was
        # rewritten by some other tool.
        records = [
            {"funding_time_ms": 100.0, "rate": 0.1},
            {"funding_time_ms": 200, "rate": 0.2},
        ]
        out = dedup_and_sort_records(records)
        assert [r["funding_time_ms"] for r in out] == [100.0, 200]

    def test_returns_new_list_not_mutating_input(self):
        records = [
            {"funding_time_ms": 200},
            {"funding_time_ms": 100},
        ]
        original = list(records)  # shallow copy of the list
        dedup_and_sort_records(records)
        assert records == original


# ---------------------------------------------------------------------------
# filter_records_by_range
# ---------------------------------------------------------------------------

class TestFilterRecordsByRange:
    def test_empty(self):
        assert filter_records_by_range([], start_ms=0, end_ms=1000) == []

    def test_inclusive_lower_bound(self):
        records = [{"funding_time_ms": 100}]
        assert filter_records_by_range(records, start_ms=100, end_ms=200) == records

    def test_inclusive_upper_bound(self):
        records = [{"funding_time_ms": 200}]
        assert filter_records_by_range(records, start_ms=100, end_ms=200) == records

    def test_exclusive_outside_range(self):
        records = [
            {"funding_time_ms": 99},
            {"funding_time_ms": 201},
        ]
        out = filter_records_by_range(records, start_ms=100, end_ms=200)
        assert out == []

    def test_multiple_records_mixed(self):
        records = [
            {"funding_time_ms": 50},
            {"funding_time_ms": 100},
            {"funding_time_ms": 150},
            {"funding_time_ms": 200},
            {"funding_time_ms": 250},
        ]
        out = filter_records_by_range(records, start_ms=100, end_ms=200)
        assert [r["funding_time_ms"] for r in out] == [100, 150, 200]

    def test_order_preserved(self):
        # Not sorted here — filter must not change relative order.
        records = [
            {"funding_time_ms": 200, "tag": "b"},
            {"funding_time_ms": 100, "tag": "a"},
            {"funding_time_ms": 150, "tag": "c"},
        ]
        out = filter_records_by_range(records, start_ms=0, end_ms=999)
        assert [r["tag"] for r in out] == ["b", "a", "c"]

    def test_invalid_records_silently_dropped(self):
        records = [
            {"funding_time_ms": 150},
            {},
            {"funding_time_ms": "bad"},
            None,
            {"funding_time_ms": 175},
        ]
        out = filter_records_by_range(records, start_ms=100, end_ms=200)
        assert [r["funding_time_ms"] for r in out] == [150, 175]

    def test_zero_width_range(self):
        # start_ms == end_ms — a valid single-tick window.
        records = [
            {"funding_time_ms": 100},
            {"funding_time_ms": 101},
        ]
        out = filter_records_by_range(records, start_ms=100, end_ms=100)
        assert [r["funding_time_ms"] for r in out] == [100]


# ---------------------------------------------------------------------------
# compute_fetch_start — the incremental decision
# ---------------------------------------------------------------------------

def _utc(y, m, d, *rest):
    return datetime(y, m, d, *rest, tzinfo=timezone.utc)


class TestComputeFetchStart:
    def test_no_cache_fetches_full_range(self):
        fetch = compute_fetch_start(
            [],
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert fetch == _utc(2024, 1, 1)

    def test_cache_fully_covers_returns_none(self):
        # Cache points span before-start and after-end → no fetch needed.
        cached = [
            to_epoch_ms(_utc(2024, 1, 1)),
            to_epoch_ms(_utc(2024, 1, 5)),
            to_epoch_ms(_utc(2024, 1, 10)),
        ]
        fetch = compute_fetch_start(
            cached,
            start=_utc(2024, 1, 2),
            end=_utc(2024, 1, 9),
        )
        assert fetch is None

    def test_cache_missing_older_refetches_from_start(self):
        # Start is before earliest cached → re-fetch from start (simpler than
        # handling a two-range request; save_cache will dedup).
        cached = [to_epoch_ms(_utc(2024, 1, 5))]
        fetch = compute_fetch_start(
            cached,
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert fetch == _utc(2024, 1, 1)

    def test_cache_missing_newer_fetches_incrementally(self):
        # End is after latest cached AND start >= earliest cached → only the
        # "tail" branch applies; fetch starts 1ms after the last cached record.
        earliest = _utc(2024, 1, 1)
        latest = _utc(2024, 1, 5, 12, 0, 0)
        cached = [to_epoch_ms(earliest), to_epoch_ms(latest)]
        fetch = compute_fetch_start(
            cached,
            start=_utc(2024, 1, 2),   # >= earliest
            end=_utc(2024, 1, 10),    # > latest
        )
        expected_ms = to_epoch_ms(latest) + 1
        assert to_epoch_ms(fetch) == expected_ms

    def test_exact_edge_coverage_does_not_trigger_fetch(self):
        # cached earliest == start_ms, cached latest == end_ms → full cover.
        cached = [
            to_epoch_ms(_utc(2024, 1, 1)),
            to_epoch_ms(_utc(2024, 1, 10)),
        ]
        fetch = compute_fetch_start(
            cached,
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert fetch is None

    def test_start_after_end_no_cache_still_returns_start(self):
        # Degenerate range — the helper doesn't validate. That's the caller's
        # job; we just need to not crash.
        fetch = compute_fetch_start(
            [],
            start=_utc(2024, 1, 10),
            end=_utc(2024, 1, 1),
        )
        assert fetch == _utc(2024, 1, 10)

    def test_naive_start_end_treated_as_utc(self):
        # A naive datetime is the common way callers forget tz. We silently
        # treat it as UTC (matching the production load_funding_rates).
        fetch = compute_fetch_start(
            [],
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 10),
        )
        assert fetch is not None
        assert fetch.tzinfo == timezone.utc

    def test_returned_fetch_is_always_utc_aware(self):
        cached = [to_epoch_ms(_utc(2024, 1, 5))]
        # Incremental path.
        fetch = compute_fetch_start(
            cached,
            start=_utc(2024, 1, 1),
            end=_utc(2024, 1, 10),
        )
        assert fetch is not None
        assert fetch.tzinfo == timezone.utc
        # No-cache path.
        fetch2 = compute_fetch_start([], start=_utc(2024, 1, 1), end=_utc(2024, 1, 10))
        assert fetch2.tzinfo == timezone.utc

    def test_priority_older_before_newer_when_both_missing(self):
        # If BOTH older and newer data are missing, the helper picks the
        # simpler "re-fetch from start" path (older rule takes precedence).
        cached = [to_epoch_ms(_utc(2024, 1, 5))]
        fetch = compute_fetch_start(
            cached,
            start=_utc(2024, 1, 1),   # before cache
            end=_utc(2024, 1, 10),    # after cache
        )
        assert fetch == _utc(2024, 1, 1)


class TestDefaults:
    def test_default_funding_interval_is_8_hours(self):
        # Constant exposed for callers assembling funding events — pins the
        # Binance perp default.
        assert DEFAULT_FUNDING_INTERVAL_MINUTES == 8 * 60
