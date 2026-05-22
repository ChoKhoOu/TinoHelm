"""Unit tests for tinohelm.core.paths — PathRegistry and PathConfigError.

Each test creates a *fresh* PathRegistry() instance to avoid cross-test
pollution.  The module-level singleton ``paths`` is NOT used here so that
override state never leaks between tests.

Covered scenarios:
    1. Unknown field → PathConfigError
    2. settings load failure → PathConfigError (fail-fast contract)
    3. override replaces settings value
    4. reset_overrides clears every override
    5. derived field (factors_dir = research / "factors")
    6a. _normalise: absolute path passes through unchanged
    6b. _normalise: relative path resolves to CWD-based absolute
    7. drift guard: _DIRECT_FIELDS == set(PathSettings.model_fields.keys())
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tinohelm.core.config import PathSettings
from tinohelm.core.paths import PathConfigError, PathRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh() -> PathRegistry:
    """Return a new PathRegistry instance with empty overrides."""
    return PathRegistry()


# ---------------------------------------------------------------------------
# 1. Unknown field raises PathConfigError
# ---------------------------------------------------------------------------

def test_unknown_field_raises_path_config_error() -> None:
    reg = fresh()
    with pytest.raises(PathConfigError) as exc_info:
        reg.get("totally_unknown_field")
    msg = str(exc_info.value)
    assert "totally_unknown_field" in msg
    # Must also list known fields so the operator can self-diagnose
    assert "strategies" in msg


# ---------------------------------------------------------------------------
# 2. settings load failure → PathConfigError (fail-fast)
# ---------------------------------------------------------------------------

def test_settings_load_failure_raises_path_config_error() -> None:
    # get_settings is imported lazily inside PathRegistry.get(), so we patch
    # it at the source module (tinohelm.core.config), not at tinohelm.core.paths.
    reg = fresh()
    with patch(
        "tinohelm.core.config.get_settings",
        side_effect=RuntimeError("simulated settings load error"),
    ):
        with pytest.raises(PathConfigError) as exc_info:
            reg.get("strategies")
    msg = str(exc_info.value)
    # Message must contain the field name and the root cause repr
    assert "strategies" in msg
    assert "simulated settings load error" in msg


# ---------------------------------------------------------------------------
# 3. override replaces settings value
# ---------------------------------------------------------------------------

def test_override_replaces_settings(tmp_path: Path) -> None:
    reg = fresh()
    target = tmp_path / "my_custom_dir"
    reg.override("funding_rates", target)
    assert reg.get("funding_rates") == target


def test_override_accepts_string(tmp_path: Path) -> None:
    """override() must coerce str to Path."""
    reg = fresh()
    target = tmp_path / "string_path"
    reg.override("catalog", str(target))
    assert reg.get("catalog") == target


# ---------------------------------------------------------------------------
# 4. reset_overrides clears all overrides
# ---------------------------------------------------------------------------

def test_reset_overrides_clears_all(tmp_path: Path) -> None:
    reg = fresh()
    reg.override("funding_rates", tmp_path / "rates")
    reg.override("catalog", tmp_path / "catalog")
    reg.reset_overrides()

    # After reset, overrides must not be active — values come from settings
    # (which have defaults, so get() will succeed).
    result = reg.get("funding_rates")
    assert result != tmp_path / "rates"


def test_reset_overrides_is_idempotent() -> None:
    """Calling reset_overrides on an empty registry must not raise."""
    reg = fresh()
    reg.reset_overrides()
    reg.reset_overrides()  # second call must also be safe


# ---------------------------------------------------------------------------
# 5. Derived field: factors_dir = research / "factors"
# ---------------------------------------------------------------------------

def test_derived_field_factors_dir(tmp_path: Path) -> None:
    reg = fresh()
    research_dir = tmp_path / "research"
    reg.override("research", research_dir)
    result = reg.get("factors_dir")
    assert result == research_dir / "factors"


def test_derived_field_universes_dir(tmp_path: Path) -> None:
    reg = fresh()
    research_dir = tmp_path / "research"
    reg.override("research", research_dir)
    result = reg.get("universes_dir")
    assert result == research_dir / "universes"


def test_derived_field_override_takes_precedence(tmp_path: Path) -> None:
    """A direct override on a derived field beats the base-field derivation."""
    reg = fresh()
    custom = tmp_path / "custom_factors"
    reg.override("factors_dir", custom)
    assert reg.get("factors_dir") == custom


# ---------------------------------------------------------------------------
# 6a. _normalise: absolute path passes through unchanged
# ---------------------------------------------------------------------------

def test_normalise_absolute_path() -> None:
    path = Path("/absolute/path/to/dir")
    result = PathRegistry._normalise(path)
    assert result == path
    assert result.is_absolute()


# ---------------------------------------------------------------------------
# 6b. _normalise: relative path resolves to CWD-based absolute
# ---------------------------------------------------------------------------

def test_normalise_relative_path() -> None:
    rel = Path("some/relative/path")
    result = PathRegistry._normalise(rel)
    assert result.is_absolute()
    # Must equal the CWD-resolved version
    assert result == rel.resolve()


# ---------------------------------------------------------------------------
# 7. Drift guard: _DIRECT_FIELDS must stay in sync with PathSettings.model_fields
# ---------------------------------------------------------------------------

def test_direct_fields_aligned_with_path_settings() -> None:
    """Catch drift if PathSettings gains or loses fields without updating _DIRECT_FIELDS."""
    model_fields = set(PathSettings.model_fields.keys())
    registry_fields = set(PathRegistry._DIRECT_FIELDS)
    assert registry_fields == model_fields, (
        f"PathRegistry._DIRECT_FIELDS is out of sync with PathSettings.model_fields.\n"
        f"In registry but not settings: {registry_fields - model_fields}\n"
        f"In settings but not registry: {model_fields - registry_fields}"
    )
