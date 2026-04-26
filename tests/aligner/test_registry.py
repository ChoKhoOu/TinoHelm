"""Tests for aligner.registry — register / resolve / list_providers."""

from __future__ import annotations

import polars as pl
import pytest

from tinohelm.aligner import register, resolve, list_providers
from tinohelm.aligner.exposure import ExposureProvider
from tinohelm.aligner import registry as _registry_module


# ---------------------------------------------------------------------------
# Helpers
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


class AnotherFakeExposure:
    name = "fake"

    def get_exposure(self, timestamps: pl.Series, symbols: list[str]) -> pl.DataFrame:
        return pl.DataFrame()


def _clear_user_providers() -> None:
    """Remove all user-registered providers between tests."""
    _registry_module._USER_PROVIDERS.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_register_and_resolve_returns_instance(tmp_path) -> None:
    """register + resolve should return a FakeExposure instance."""
    _clear_user_providers()
    register("fake", FakeExposure)
    obj = resolve("fake")
    assert isinstance(obj, FakeExposure)
    assert isinstance(obj, ExposureProvider)
    _clear_user_providers()


def test_resolve_returns_fresh_instance_each_call() -> None:
    """resolve must instantiate a new object on every call."""
    _clear_user_providers()
    register("fake", FakeExposure)
    a = resolve("fake")
    b = resolve("fake")
    assert a is not b
    _clear_user_providers()


def test_duplicate_registration_same_class_is_idempotent() -> None:
    """Registering the same name + same class twice must not raise."""
    _clear_user_providers()
    register("fake", FakeExposure)
    register("fake", FakeExposure)  # idempotent — same class, should not raise
    _clear_user_providers()


def test_duplicate_registration_different_class_raises() -> None:
    """Registering the same name with a different class must raise ValueError."""
    _clear_user_providers()
    register("fake", FakeExposure)
    with pytest.raises(ValueError, match="already registered"):
        register("fake", AnotherFakeExposure)
    _clear_user_providers()


def test_resolve_unknown_name_raises_key_error() -> None:
    """Resolving an unregistered name must raise KeyError."""
    _clear_user_providers()
    with pytest.raises(KeyError, match="unknown_provider_xyz"):
        resolve("unknown_provider_xyz")


def test_list_providers_includes_builtins() -> None:
    """list_providers must include the builtin provider names."""
    _clear_user_providers()
    providers = list_providers()
    assert "btc_beta" in providers
    assert "log_mcap" in providers


def test_list_providers_is_sorted() -> None:
    """list_providers must return a sorted list."""
    _clear_user_providers()
    providers = list_providers()
    assert providers == sorted(providers)


def test_list_providers_includes_user_registered() -> None:
    """list_providers must include user-registered providers."""
    _clear_user_providers()
    register("fake", FakeExposure)
    providers = list_providers()
    assert "fake" in providers
    _clear_user_providers()


def test_resolve_builtin_btc_beta() -> None:
    """resolve('btc_beta') must return a BTCBetaExposure-compatible instance."""
    _clear_user_providers()
    obj = resolve("btc_beta")
    assert isinstance(obj, ExposureProvider)
    assert obj.name == "btc_beta"


def test_resolve_builtin_log_mcap() -> None:
    """resolve('log_mcap') must return a LogMcapExposure-compatible instance."""
    _clear_user_providers()
    obj = resolve("log_mcap")
    assert isinstance(obj, ExposureProvider)
    assert obj.name == "log_mcap"


def test_register_builtin_name_with_different_class_raises() -> None:
    """Registering an existing builtin name with a different class must raise ValueError."""
    _clear_user_providers()

    class CustomBTCBeta:
        name = "btc_beta"

        def get_exposure(self, timestamps, symbols):
            return pl.DataFrame()

    with pytest.raises(ValueError, match="already registered"):
        register("btc_beta", CustomBTCBeta)
    _clear_user_providers()
