"""Tests for ExposureProvider Protocol — runtime_checkable structural check."""

from __future__ import annotations

import polars as pl
import pytest

from tinohelm.aligner.exposure import ExposureProvider, BTCBetaExposure, LogMcapExposure


# ---------------------------------------------------------------------------
# Fake provider that satisfies the Protocol structurally
# ---------------------------------------------------------------------------


class FakeExposure:
    name = "fake"

    def get_exposure(
        self,
        timestamps: pl.Series,
        symbols: list[str],
    ) -> pl.DataFrame:
        return pl.DataFrame(
            {"ts": timestamps, **{s: [0.0] * len(timestamps) for s in symbols}}
        )


class _MissingNameExposure:
    """Satisfies get_exposure but lacks ``name``."""

    def get_exposure(self, timestamps: pl.Series, symbols: list[str]) -> pl.DataFrame:
        return pl.DataFrame()


class _MissingMethodExposure:
    """Has ``name`` but lacks ``get_exposure``."""

    name = "incomplete"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fake_provider_is_instance() -> None:
    """A class with ``name`` + ``get_exposure`` satisfies ExposureProvider."""
    obj = FakeExposure()
    assert isinstance(obj, ExposureProvider)


def test_missing_name_fails_runtime_check() -> None:
    """Object without ``name`` attribute does not satisfy the Protocol."""
    obj = _MissingNameExposure()
    assert not isinstance(obj, ExposureProvider)


def test_missing_method_fails_runtime_check() -> None:
    """Object without ``get_exposure`` does not satisfy the Protocol."""
    obj = _MissingMethodExposure()
    assert not isinstance(obj, ExposureProvider)


def test_btc_beta_exposure_is_instance() -> None:
    """BTCBetaExposure skeleton satisfies ExposureProvider."""
    obj = BTCBetaExposure()
    assert isinstance(obj, ExposureProvider)


def test_log_mcap_exposure_is_instance() -> None:
    """LogMcapExposure skeleton satisfies ExposureProvider."""
    obj = LogMcapExposure()
    assert isinstance(obj, ExposureProvider)


def test_fake_provider_get_exposure_shape() -> None:
    """FakeExposure.get_exposure returns correct shape (T rows, ts + N cols)."""
    ts = pl.Series("ts", [1, 2, 3])
    symbols = ["BTCUSDT-PERP", "ETHUSDT-PERP"]
    obj = FakeExposure()
    df = obj.get_exposure(ts, symbols)
    assert df.shape == (3, 3)  # 3 rows, ts + 2 symbol cols
    assert df.columns[0] == "ts"
    assert set(df.columns[1:]) == set(symbols)


def test_btc_beta_is_callable() -> None:
    """BTCBetaExposure.get_exposure is implemented (s07) and does not raise NotImplementedError."""
    obj = BTCBetaExposure()
    assert callable(obj.get_exposure)
    assert obj.name == "btc_beta"


def test_log_mcap_is_callable() -> None:
    """LogMcapExposure.get_exposure is implemented (s07) and does not raise NotImplementedError."""
    obj = LogMcapExposure()
    assert callable(obj.get_exposure)
    assert obj.name == "log_mcap"
