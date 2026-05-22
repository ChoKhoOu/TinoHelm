"""Unit tests for ``tinohelm.factor.observer``.

Coverage
--------
- Nested spans: data_load > kernel_exec; summary reflects parent_id hierarchy
- duration_ms > 0 for each span (verified with a small sleep)
- output_stats recorded via record_output_stats; NaN rate present in summary
- Exception inside ``with`` block → span error field is populated; exception
  still propagates (not suppressed)
- run_id propagation
- Structured JSON log emitted on end_span
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd
import pytest

from tinohelm.factor.observer import Observer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_panel(rows: int = 10, cols: int = 3,
                nan_frac: float = 0.0) -> pd.DataFrame:
    """Create a simple synthetic panel (time × symbol)."""
    rng = np.random.default_rng(42)
    data = rng.standard_normal((rows, cols))
    if nan_frac > 0:
        n_nan = max(1, int(rows * cols * nan_frac))
        flat_idx = rng.choice(rows * cols, n_nan, replace=False)
        data.flat[flat_idx] = np.nan
    index = pd.date_range("2025-01-01", periods=rows, freq="D")
    cols_names = [f"SYM{i}" for i in range(cols)]
    return pd.DataFrame(data, index=index, columns=cols_names)


def _capture_logger() -> tuple[logging.Logger, list[str]]:
    """Return a logger + a list that accumulates all logged messages."""
    name = f"test_observer_{id(object())}"
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    messages: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(self.format(record))

    handler = _Handler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger, messages


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNestedSpans:
    """data_load outer span contains kernel_exec inner span (parent_id link)."""

    def test_nested_parent_id(self):
        obs = Observer(run_id="test-run-1")
        with obs.start_span("data_load") as outer:
            time.sleep(0.001)
            with obs.start_span("kernel_exec", factor="ret_20") as inner:
                time.sleep(0.001)

        summary = obs.summary()
        spans = {s["name"]: s for s in summary["spans"]}

        assert "data_load" in spans
        assert "kernel_exec" in spans

        # inner span's parent_id must equal outer span's span_id
        assert spans["kernel_exec"]["parent_id"] == spans["data_load"]["span_id"]
        # outer span has no parent
        assert spans["data_load"]["parent_id"] is None

    def test_two_spans_recorded(self):
        obs = Observer(run_id="test-run-2")
        with obs.start_span("data_load"):
            time.sleep(0.001)
            with obs.start_span("kernel_exec"):
                time.sleep(0.001)

        assert len(obs.summary()["spans"]) == 2

    def test_run_id_in_summary(self):
        obs = Observer(run_id="explicit-id")
        with obs.start_span("x"):
            pass
        assert obs.summary()["run_id"] == "explicit-id"


class TestDuration:
    """Each span must have duration_ms > 0."""

    def test_duration_positive_outer(self):
        obs = Observer()
        with obs.start_span("data_load"):
            time.sleep(0.001)
            with obs.start_span("kernel_exec"):
                time.sleep(0.001)

        for span in obs.summary()["spans"]:
            assert span["duration_ms"] > 0, (
                f"span '{span['name']}' has duration_ms={span['duration_ms']}"
            )

    def test_start_ts_before_end_ts(self):
        obs = Observer()
        with obs.start_span("s1"):
            time.sleep(0.001)
        span = obs.summary()["spans"][0]
        assert span["end_ts"] > span["start_ts"]


class TestOutputStats:
    """record_output_stats writes per-factor stats; summary contains them."""

    def test_stats_present_after_record(self):
        obs = Observer()
        panel = _make_panel(10, 3, nan_frac=0.0)
        obs.record_output_stats("ret_20", panel)
        summary = obs.summary()

        assert "ret_20" in summary["output_stats"]

    def test_nan_rate_field_present(self):
        obs = Observer()
        panel = _make_panel()
        obs.record_output_stats("ret_20", panel)
        stats = obs.summary()["output_stats"]["ret_20"]

        assert "nan_rate" in stats

    def test_nan_rate_zero_for_clean_panel(self):
        obs = Observer()
        panel = _make_panel(nan_frac=0.0)
        obs.record_output_stats("clean_factor", panel)
        stats = obs.summary()["output_stats"]["clean_factor"]

        assert stats["nan_rate"] == pytest.approx(0.0)

    def test_nan_rate_nonzero_for_dirty_panel(self):
        obs = Observer()
        panel = _make_panel(rows=20, cols=5, nan_frac=0.3)
        obs.record_output_stats("dirty_factor", panel)
        stats = obs.summary()["output_stats"]["dirty_factor"]

        assert stats["nan_rate"] > 0.0

    def test_shape_correct(self):
        obs = Observer()
        panel = _make_panel(rows=15, cols=4)
        obs.record_output_stats("f1", panel)
        assert obs.summary()["output_stats"]["f1"]["shape"] == [15, 4]

    def test_min_max_mean_std_present(self):
        obs = Observer()
        panel = _make_panel()
        obs.record_output_stats("f2", panel)
        stats = obs.summary()["output_stats"]["f2"]

        for key in ("min", "max", "mean", "std"):
            assert key in stats
            assert stats[key] is not None

    def test_nonzero_rate_present(self):
        obs = Observer()
        panel = _make_panel()
        obs.record_output_stats("f3", panel)
        stats = obs.summary()["output_stats"]["f3"]

        assert "nonzero_rate" in stats
        assert 0.0 <= stats["nonzero_rate"] <= 1.0


class TestErrorCapture:
    """Exception inside ``with`` block → span records error; exception re-raised."""

    def test_exception_recorded_in_span(self):
        obs = Observer()
        with pytest.raises(ValueError, match="boom"):
            with obs.start_span("failing_span", factor="bad_factor"):
                time.sleep(0.001)
                raise ValueError("boom")

        summary = obs.summary()
        assert len(summary["spans"]) == 1
        span = summary["spans"][0]
        assert span["error"] is not None
        assert "ValueError" in span["error"]
        assert "boom" in span["error"]

    def test_error_span_duration_positive(self):
        obs = Observer()
        with pytest.raises(RuntimeError):
            with obs.start_span("err_span"):
                time.sleep(0.001)
                raise RuntimeError("fail")

        span = obs.summary()["spans"][0]
        assert span["duration_ms"] > 0

    def test_error_does_not_suppress_exception(self):
        """Span context manager must NOT suppress exceptions."""
        obs = Observer()
        caught = False
        try:
            with obs.start_span("s"):
                raise KeyError("propagate_me")
        except KeyError:
            caught = True
        assert caught

    def test_successful_span_has_no_error(self):
        obs = Observer()
        with obs.start_span("ok_span"):
            time.sleep(0.001)
        span = obs.summary()["spans"][0]
        assert span["error"] is None


class TestStructuredLogging:
    """end_span emits a JSON-parseable log line via the injected logger."""

    def test_log_line_is_valid_json(self):
        logger, messages = _capture_logger()
        obs = Observer(run_id="log-test", logger=logger)
        with obs.start_span("log_span"):
            time.sleep(0.001)

        assert len(messages) == 1
        payload = json.loads(messages[0])
        assert payload["event"] == "span_end"

    def test_log_contains_run_id(self):
        logger, messages = _capture_logger()
        obs = Observer(run_id="my-run", logger=logger)
        with obs.start_span("x"):
            pass
        payload = json.loads(messages[0])
        assert payload["run_id"] == "my-run"

    def test_log_contains_name(self):
        logger, messages = _capture_logger()
        obs = Observer(logger=logger)
        with obs.start_span("my_span"):
            pass
        payload = json.loads(messages[0])
        assert payload["name"] == "my_span"

    def test_log_contains_tags(self):
        logger, messages = _capture_logger()
        obs = Observer(logger=logger)
        with obs.start_span("tagged", factor="ret_5", stage=2):
            pass
        payload = json.loads(messages[0])
        assert payload["tags"]["factor"] == "ret_5"
        assert payload["tags"]["stage"] == 2

    def test_nested_spans_emit_two_log_lines(self):
        logger, messages = _capture_logger()
        obs = Observer(logger=logger)
        with obs.start_span("outer"):
            time.sleep(0.001)
            with obs.start_span("inner"):
                time.sleep(0.001)

        assert len(messages) == 2
        names = {json.loads(m)["name"] for m in messages}
        assert names == {"outer", "inner"}


class TestAutoRunId:
    """When run_id is not supplied, a UUID is auto-generated."""

    def test_auto_run_id_is_string(self):
        obs = Observer()
        assert isinstance(obs.run_id, str)
        assert len(obs.run_id) > 0

    def test_two_observers_have_different_run_ids(self):
        obs1 = Observer()
        obs2 = Observer()
        assert obs1.run_id != obs2.run_id
