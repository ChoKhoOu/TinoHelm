"""Unit tests — ``tinohelm.signal.decorator`` and
``tinohelm.signal.registry``.

Coverage
--------
- AC-3.1.1: ``@signal`` attaches a frozen :class:`SignalSpec` whose
  ``code_hash`` is non-empty and whose declared fields are preserved.
- AC-3.1.2: ``code_hash`` is deterministic across imports of the same
  source, and changes when the source body changes.
- ``extra_warmup_bars`` defaults to 0 and accepts positive overrides.
- Numeric guards in the decorator raise ``ValueError`` for
  zero/negative ``gross_exposure`` / ``max_position`` and for negative
  ``net_exposure`` / ``extra_warmup_bars``.
- :class:`SignalRegistry` discovers ``@signal``-decorated functions in
  user ``.py`` files inside ``signals_dir`` and exposes them via
  ``get_kernel`` / ``get_spec`` / ``list_signals``.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import sys
import textwrap
from pathlib import Path

import pytest

from tinohelm.signal import CostModel, SignalRegistry, SignalSpec, signal


# ---------------------------------------------------------------------------
# AC-3.1.1: decorator attaches frozen SignalSpec with non-empty code_hash
# ---------------------------------------------------------------------------


class TestSignalDecoratorBasic:
    def test_spec_attached(self):
        @signal(
            name="my_sig",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="top10_perp",
            method_params={"k": 3},
        )
        def my_signal(factor_panel):
            return factor_panel

        assert hasattr(my_signal, "__signal_spec__")
        assert isinstance(my_signal.__signal_spec__, SignalSpec)

    def test_spec_field_values_preserved(self):
        @signal(
            name="my_sig",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="top10_perp",
            method_params={"k": 3},
            description="my description",
            version="2.1.0",
        )
        def my_signal(factor_panel):
            return factor_panel

        spec = my_signal.__signal_spec__
        assert spec.name == "my_sig"
        assert spec.factor_ref == "ret_N@1.0.0"
        assert spec.method == "top_k_long_short"
        assert spec.weighting == "equal"  # default
        assert spec.rebalance_freq == "1D"
        assert spec.universe_ref == "top10_perp"
        assert spec.method_params == {"k": 3}
        assert spec.description == "my description"
        assert spec.version == "2.1.0"
        assert isinstance(spec.cost_model, CostModel)
        assert spec.cost_model.name == "taker_8bps"

    def test_spec_is_frozen(self):
        @signal(
            name="my_sig",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="top10_perp",
        )
        def my_signal(factor_panel):
            return factor_panel

        spec = my_signal.__signal_spec__
        # frozen=True dataclass raises FrozenInstanceError on assignment
        with pytest.raises(Exception):  # FrozenInstanceError or TypeError
            spec.name = "renamed"  # type: ignore[misc]

    def test_code_hash_nonempty(self):
        @signal(
            name="my_sig",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="top10_perp",
        )
        def my_signal(factor_panel):
            return factor_panel

        h = my_signal.__signal_spec__.code_hash
        assert h != ""
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_decorator_returns_unchanged_function(self):
        """The decorator MUST return the original function unchanged
        (not a wrapper) — mirrors @factor's contract.
        """

        def original(factor_panel):
            return factor_panel

        decorated = signal(
            name="my_sig",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="top10_perp",
        )(original)
        assert decorated is original
        assert decorated(42) == 42  # original behaviour preserved


# ---------------------------------------------------------------------------
# AC-3.1.2: code_hash determinism + responds to source changes
# ---------------------------------------------------------------------------


class TestCodeHashSemantics:
    def test_code_hash_deterministic(self):
        """Decorating the same function twice yields the same hash."""

        def k(factor_panel):
            return factor_panel

        s1 = signal(
            name="a",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
        )(k).__signal_spec__
        # Reset attribute and re-decorate the same source object.
        # (The decorator overwrites __signal_spec__ on the same function.)
        s2 = signal(
            name="a",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
        )(k).__signal_spec__
        assert s1.code_hash == s2.code_hash

    def test_code_hash_matches_manual_sha256(self):
        @signal(
            name="x",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
        )
        def kernel(factor_panel):
            return factor_panel

        expected = hashlib.sha256(
            inspect.getsource(kernel).encode("utf-8")
        ).hexdigest()
        assert kernel.__signal_spec__.code_hash == expected

    def test_code_hash_changes_with_source(self):
        """Two functions with different bodies must have different hashes."""

        @signal(
            name="v1",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
        )
        def kernel_v1(factor_panel):
            return factor_panel  # body version 1

        @signal(
            name="v2",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
        )
        def kernel_v2(factor_panel):
            return factor_panel.with_columns()  # body version 2 — different

        assert (
            kernel_v1.__signal_spec__.code_hash
            != kernel_v2.__signal_spec__.code_hash
        )

    def test_code_hash_stable_across_module_reimport(self, tmp_path: Path):
        """Loading the same source from a file twice yields the same hash."""

        py_src = textwrap.dedent(
            '''
            from tinohelm.signal import signal

            @signal(name="reimport", factor_ref="ret_N@1.0.0",
                    method="top_k_long_short", rebalance_freq="1D",
                    universe_ref="u")
            def k(factor_panel):
                return factor_panel
            '''
        )
        f = tmp_path / "reimport_signal.py"
        f.write_text(py_src)

        # First import
        spec_loader = importlib.util.spec_from_file_location(
            "_reimport_signal_1", f
        )
        m1 = importlib.util.module_from_spec(spec_loader)  # type: ignore[arg-type]
        sys.modules["_reimport_signal_1"] = m1
        spec_loader.loader.exec_module(m1)  # type: ignore[union-attr]
        h1 = m1.k.__signal_spec__.code_hash

        # Second import (different module name, same file content)
        spec_loader2 = importlib.util.spec_from_file_location(
            "_reimport_signal_2", f
        )
        m2 = importlib.util.module_from_spec(spec_loader2)  # type: ignore[arg-type]
        sys.modules["_reimport_signal_2"] = m2
        spec_loader2.loader.exec_module(m2)  # type: ignore[union-attr]
        h2 = m2.k.__signal_spec__.code_hash

        assert h1 == h2

        # Now perturb the source by changing the body; hash should change.
        py_src_v2 = textwrap.dedent(
            '''
            from tinohelm.signal import signal

            @signal(name="reimport", factor_ref="ret_N@1.0.0",
                    method="top_k_long_short", rebalance_freq="1D",
                    universe_ref="u")
            def k(factor_panel):
                # add a comment so the source bytes differ
                return factor_panel
            '''
        )
        f.write_text(py_src_v2)
        spec_loader3 = importlib.util.spec_from_file_location(
            "_reimport_signal_3", f
        )
        m3 = importlib.util.module_from_spec(spec_loader3)  # type: ignore[arg-type]
        sys.modules["_reimport_signal_3"] = m3
        spec_loader3.loader.exec_module(m3)  # type: ignore[union-attr]
        h3 = m3.k.__signal_spec__.code_hash
        assert h3 != h1


# ---------------------------------------------------------------------------
# extra_warmup_bars + numeric guards
# ---------------------------------------------------------------------------


class TestExtraWarmupAndGuards:
    def test_extra_warmup_bars_default_zero(self):
        @signal(
            name="x",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
        )
        def k(factor_panel):
            return factor_panel

        assert k.__signal_spec__.extra_warmup_bars == 0

    def test_extra_warmup_bars_positive(self):
        @signal(
            name="x",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
            extra_warmup_bars=5,
        )
        def k(factor_panel):
            return factor_panel

        assert k.__signal_spec__.extra_warmup_bars == 5

    def test_negative_extra_warmup_rejected(self):
        with pytest.raises(ValueError, match="extra_warmup_bars"):

            @signal(
                name="x",
                factor_ref="ret_N@1.0.0",
                method="top_k_long_short",
                rebalance_freq="1D",
                universe_ref="u",
                extra_warmup_bars=-1,
            )
            def k(factor_panel):
                return factor_panel

    def test_zero_gross_exposure_rejected(self):
        with pytest.raises(ValueError, match="gross_exposure"):

            @signal(
                name="x",
                factor_ref="ret_N@1.0.0",
                method="top_k_long_short",
                rebalance_freq="1D",
                universe_ref="u",
                gross_exposure=0.0,
            )
            def k(factor_panel):
                return factor_panel

    def test_zero_max_position_rejected(self):
        with pytest.raises(ValueError, match="max_position"):

            @signal(
                name="x",
                factor_ref="ret_N@1.0.0",
                method="top_k_long_short",
                rebalance_freq="1D",
                universe_ref="u",
                max_position=0.0,
            )
            def k(factor_panel):
                return factor_panel

    def test_negative_net_exposure_rejected(self):
        with pytest.raises(ValueError, match="net_exposure"):

            @signal(
                name="x",
                factor_ref="ret_N@1.0.0",
                method="top_k_long_short",
                rebalance_freq="1D",
                universe_ref="u",
                net_exposure=-0.1,
            )
            def k(factor_panel):
                return factor_panel

    def test_zero_turnover_budget_rejected(self):
        with pytest.raises(ValueError, match="turnover_budget"):

            @signal(
                name="x",
                factor_ref="ret_N@1.0.0",
                method="top_k_long_short",
                rebalance_freq="1D",
                universe_ref="u",
                turnover_budget=0.0,
            )
            def k(factor_panel):
                return factor_panel

    def test_none_turnover_budget_allowed(self):
        @signal(
            name="x",
            factor_ref="ret_N@1.0.0",
            method="top_k_long_short",
            rebalance_freq="1D",
            universe_ref="u",
            turnover_budget=None,
        )
        def k(factor_panel):
            return factor_panel

        assert k.__signal_spec__.turnover_budget is None


# ---------------------------------------------------------------------------
# SignalRegistry — user-dir scanning
# ---------------------------------------------------------------------------


class TestSignalRegistry:
    def test_scan_empty_dir(self, tmp_path: Path):
        empty_dir = tmp_path / "signals"
        empty_dir.mkdir()
        reg = SignalRegistry(signals_dir=empty_dir)
        assert reg.scan() == {}
        assert reg.list_signals() == []

    def test_scan_missing_dir(self, tmp_path: Path):
        missing = tmp_path / "does_not_exist"
        reg = SignalRegistry(signals_dir=missing)
        assert reg.scan() == {}
        assert reg.list_signals() == []

    def test_scan_user_signal(self, tmp_path: Path):
        signals_dir = tmp_path / "signals"
        signals_dir.mkdir()
        (signals_dir / "my_sig.py").write_text(
            textwrap.dedent(
                '''
                from tinohelm.signal import signal

                @signal(
                    name="my_user_signal",
                    factor_ref="ret_N@1.0.0",
                    method="top_k_long_short",
                    rebalance_freq="1D",
                    universe_ref="top10_perp",
                    method_params={"k": 5},
                )
                def my_user_signal(factor_panel):
                    return factor_panel
                '''
            )
        )

        reg = SignalRegistry(signals_dir=signals_dir)
        specs = reg.scan()
        assert "my_user_signal" in specs
        assert "my_user_signal" in reg.list_signals()

        spec = reg.get_spec("my_user_signal")
        assert spec is not None
        assert spec.method == "top_k_long_short"
        assert spec.method_params == {"k": 5}

        kernel = reg.get_kernel("my_user_signal")
        assert callable(kernel)
        # The decorator returns the original function unchanged → calling
        # the registered kernel must work transparently.
        assert kernel(42) == 42  # noqa: PLR2004 — sanity check

    def test_skips_underscore_files(self, tmp_path: Path):
        """Files starting with ``_`` (e.g. _draft.py) must not be loaded."""
        signals_dir = tmp_path / "signals"
        signals_dir.mkdir()
        (signals_dir / "_draft.py").write_text(
            textwrap.dedent(
                '''
                from tinohelm.signal import signal

                @signal(name="draft_sig", factor_ref="ret_N@1.0.0",
                        method="top_k_long_short", rebalance_freq="1D",
                        universe_ref="u")
                def k(factor_panel):
                    return factor_panel
                '''
            )
        )
        reg = SignalRegistry(signals_dir=signals_dir)
        assert reg.scan() == {}

    def test_get_kernel_unknown_raises(self, tmp_path: Path):
        empty = tmp_path / "signals"
        empty.mkdir()
        reg = SignalRegistry(signals_dir=empty)
        reg.scan()
        with pytest.raises(KeyError, match="not found in registry"):
            reg.get_kernel("nonexistent_signal")

    def test_get_spec_unknown_returns_none(self, tmp_path: Path):
        empty = tmp_path / "signals"
        empty.mkdir()
        reg = SignalRegistry(signals_dir=empty)
        reg.scan()
        assert reg.get_spec("nonexistent") is None

    def test_paths_get_signals_dir_resolves(self):
        """``paths.get('signals_dir')`` is registered as a derived field."""
        from tinohelm.core.paths import paths

        result = paths.get("signals_dir")
        assert result.name == "signals"
        # signals_dir is research/signals — the parent must be the research dir.
        assert result.parent == paths.get("research")
