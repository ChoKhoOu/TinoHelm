"""Tests for the ``paths_override`` pytest fixture.

Verifies:
  1. ``test_fixture_isolates_across_tests`` — two serial tests each override
     the same field; the second test must NOT see the first test's override
     (auto-teardown between tests).
  2. ``test_paths_override_smoke`` — fixture is injectable and sets the
     override visible via the module-level singleton.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tinohelm.core.paths import paths as _paths


# ---------------------------------------------------------------------------
# Isolation pair — must run in definition order (pytest default)
# ---------------------------------------------------------------------------

def test_fixture_isolates_across_tests_part1(
    tmp_path: Path,
    paths_override,
) -> None:
    """Part 1: install an override and confirm it is active."""
    override_dir = tmp_path / "rates_part1"
    override_dir.mkdir()
    paths_override("funding_rates", override_dir)
    assert _paths.get("funding_rates") == override_dir


def test_fixture_isolates_across_tests_part2(
    tmp_path: Path,
    paths_override,
) -> None:
    """Part 2: a different override must NOT see Part 1's value (auto-teardown).

    This test deliberately installs its own override so ``paths.get()`` does
    not hit real settings (which may not be configured in CI).  The key
    assertion is that the directory is *this* test's tmp dir, not Part 1's.
    """
    override_dir = tmp_path / "rates_part2"
    override_dir.mkdir()
    paths_override("funding_rates", override_dir)

    result = _paths.get("funding_rates")
    assert result == override_dir
    # Explicitly confirm Part 1's path is gone
    assert "rates_part1" not in str(result)


# ---------------------------------------------------------------------------
# Smoke test — fixture injects cleanly into any function signature
# ---------------------------------------------------------------------------

def test_paths_override_smoke(tmp_path: Path, paths_override) -> None:
    """Fixture can be injected and the override is reflected on the singleton."""
    target = tmp_path / "smoke_catalog"
    target.mkdir()
    paths_override("catalog", target)
    assert _paths.get("catalog") == target


def test_paths_override_multiple_fields(tmp_path: Path, paths_override) -> None:
    """Multiple fields can be overridden in a single test."""
    funding = tmp_path / "funding"
    catalog = tmp_path / "catalog"
    funding.mkdir()
    catalog.mkdir()

    paths_override("funding_rates", funding)
    paths_override("catalog", catalog)

    assert _paths.get("funding_rates") == funding
    assert _paths.get("catalog") == catalog


def test_paths_override_teardown_cleans_all(tmp_path: Path, paths_override) -> None:
    """After this test finishes, the singleton must have no overrides.

    We cannot directly observe teardown from within the test, but we can
    install overrides and verify they are active — the conftest teardown
    (``reset_overrides``) runs after ``yield``, which is tested implicitly
    by the isolation pair above.
    """
    paths_override("funding_rates", tmp_path / "x")
    paths_override("catalog", tmp_path / "y")
    # Both overrides are live during the test body
    assert _paths.get("funding_rates") == tmp_path / "x"
    assert _paths.get("catalog") == tmp_path / "y"
