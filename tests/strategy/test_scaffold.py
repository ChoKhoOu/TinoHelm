"""Tests for ``tinohelm.strategy.scaffold`` + ``scaffold_helpers``.

The scaffold module is the user-facing code-generation path reached by
``POST /api/strategies/create``. A regression here silently produces broken
strategy files that users only discover when they try to run them. These
tests pin the contract at each observable boundary:

* identifier validation
* snake→PascalCase conversion
* the template renders to *parseable Python* for representative names
* path traversal is rejected via :func:`is_within_dir` (not the legacy
  ``str().startswith()`` pattern)
* ``generate_scaffold`` is a thin orchestrator — failure modes, exit codes
  and return paths are all locked down

All tests are **NT-free**: no NautilusTrader import occurs during collection
or execution, so this file runs in the lean CI image.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from tinohelm.strategy import scaffold
from tinohelm.strategy.scaffold import STRATEGY_SCAFFOLD, generate_scaffold
from tinohelm.strategy.scaffold_helpers import (
    IDENTIFIER_RE,
    derive_class_name,
    render_scaffold,
    resolve_new_strategy_path,
    validate_identifier,
)


# ---------------------------------------------------------------------------
# Helpers: identifier regex + validation
# ---------------------------------------------------------------------------


class TestIdentifierRegex:
    """Behavioural contract for :data:`IDENTIFIER_RE`."""

    @pytest.mark.parametrize(
        "name",
        ["x", "X", "_", "abc", "abc123", "_leading", "snake_case", "__dunder__", "ABC"],
    )
    def test_matches_valid(self, name: str):
        assert IDENTIFIER_RE.match(name)

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "1abc",          # starts with digit
            "abc-def",       # hyphen
            "abc def",       # space
            "abc.def",       # dot (path)
            "../escape",     # path traversal attempt
            "foo/bar",       # slash
            "naïve",         # non-ASCII
            "foo\nbar",      # newline
            "foo$",          # punctuation
        ],
    )
    def test_rejects_invalid(self, name: str):
        assert IDENTIFIER_RE.match(name) is None


class TestValidateIdentifier:

    def test_accepts_valid(self):
        # Should not raise.
        validate_identifier("my_strategy")
        validate_identifier("_private")
        validate_identifier("StrategyV2")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="valid Python identifier"):
            validate_identifier("")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="valid Python identifier"):
            validate_identifier("../etc/passwd")

    def test_rejects_starting_digit(self):
        with pytest.raises(ValueError, match="valid Python identifier"):
            validate_identifier("1strategy")

    def test_rejects_non_string(self):
        with pytest.raises(ValueError, match="valid Python identifier"):
            validate_identifier(None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="valid Python identifier"):
            validate_identifier(42)  # type: ignore[arg-type]

    def test_error_message_includes_repr(self):
        # The message should quote the bad input so the API can surface it.
        with pytest.raises(ValueError) as ei:
            validate_identifier("bad-name")
        assert "bad-name" in str(ei.value)


# ---------------------------------------------------------------------------
# Helpers: class-name derivation
# ---------------------------------------------------------------------------


class TestDeriveClassName:

    @pytest.mark.parametrize(
        "snake, pascal",
        [
            ("my_strategy", "MyStrategy"),
            ("foo_bar_baz", "FooBarBaz"),
            ("simple", "Simple"),
            ("abc", "Abc"),
            ("x", "X"),
        ],
    )
    def test_converts_snake_to_pascal(self, snake: str, pascal: str):
        assert derive_class_name(snake) == pascal

    def test_capitalize_lowercases_tail(self):
        """``str.capitalize()`` uppercases the first char and **lowercases**
        the rest — lock this in so ``BTC_Scalper`` becomes ``BtcScalper``
        (not ``BTCScalper``). Readers surprised by this can see the pinned
        contract here rather than chase through Python docs.
        """
        assert derive_class_name("BTC_Scalper") == "BtcScalper"
        assert derive_class_name("HTTP_server") == "HttpServer"

    def test_leading_underscore_collapses(self):
        # "".capitalize() == "" — historical behaviour preserved.
        assert derive_class_name("_foo") == "Foo"

    def test_trailing_underscore_collapses(self):
        assert derive_class_name("foo_") == "Foo"

    def test_consecutive_underscores_collapse(self):
        assert derive_class_name("foo__bar") == "FooBar"

    def test_empty_string(self):
        assert derive_class_name("") == ""


# ---------------------------------------------------------------------------
# Helpers: template rendering
# ---------------------------------------------------------------------------


class TestRenderScaffold:

    def test_returns_syntactically_valid_python(self):
        # This is the single most important invariant: whatever a user types
        # as the strategy name, the generated file must at least *parse*.
        content = render_scaffold("my_cool_strat")
        ast.parse(content)

    def test_name_substitutes_into_docstring(self):
        content = render_scaffold("my_cool_strat")
        assert "my_cool_strat" in content

    def test_class_name_substitutes_into_class_header(self):
        content = render_scaffold("my_cool_strat")
        assert "class MyCoolStrat(" in content
        assert "class MyCoolStratConfig(" in content

    def test_template_is_deterministic(self):
        # Same input → byte-for-byte identical output (no timestamps etc.).
        a = render_scaffold("abc")
        b = render_scaffold("abc")
        assert a == b

    def test_different_names_differ(self):
        a = render_scaffold("foo")
        b = render_scaffold("bar")
        assert a != b
        assert "class Foo(" in a and "class Foo(" not in b
        assert "class Bar(" in b and "class Bar(" not in a

    def test_single_char_name_renders(self):
        content = render_scaffold("x")
        ast.parse(content)
        assert "class X(" in content

    def test_all_f_string_braces_closed(self):
        # The template embeds many log f-strings using ``{{...}}`` escapes.
        # If a ``{{`` were missed, ``.format()`` would raise KeyError, not
        # produce malformed output — but we still pin the presence of
        # rendered single-brace f-expressions.
        content = render_scaffold("x")
        assert 'self.log.error(f"品种未找到: {nt_sym}")' in content
        assert 'self.log.info(f"启动，品种: {self.symbols}")' in content

    def test_template_length_stable(self):
        # Sanity: scaffolds are large (~20k chars). If this halves we've
        # accidentally truncated the template string literal during editing.
        content = render_scaffold("x")
        assert 19_000 < len(content) < 22_000


# ---------------------------------------------------------------------------
# Helpers: safe path resolution
# ---------------------------------------------------------------------------


class TestResolveNewStrategyPath:

    def test_happy_path(self, tmp_path: Path):
        p = resolve_new_strategy_path(tmp_path, "my_strat")
        assert p == (tmp_path / "my_strat.py").resolve()

    def test_returns_resolved_path(self, tmp_path: Path):
        # Input dir is not necessarily already-resolved; helper normalises.
        p = resolve_new_strategy_path(str(tmp_path), "foo")
        assert p.is_absolute()
        assert p.parent == tmp_path.resolve()

    def test_non_existent_strategies_dir_still_resolves(self, tmp_path: Path):
        # resolve_new_strategy_path does NOT require the dir to exist; it is
        # the caller's job to mkdir. We still want Path.resolve() to succeed.
        d = tmp_path / "not-yet"
        p = resolve_new_strategy_path(d, "foo")
        assert p.parent == d.resolve()

    def test_boundary_escape_via_dotdot_raises(self, tmp_path: Path):
        # Even though validate_identifier would reject this upstream, the
        # helper is defence-in-depth. We call it directly here.
        with pytest.raises(ValueError, match="Path traversal"):
            resolve_new_strategy_path(tmp_path, "../etc/passwd")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
    def test_symlink_escape_rejected(self, tmp_path: Path):
        """A symlink that points outside the strategies dir is caught."""
        outside = tmp_path / "outside"
        outside.mkdir()
        inside = tmp_path / "strategies"
        inside.mkdir()
        # Create a symlink *inside* the strategies dir that escapes.
        escape_link = inside / "escape"
        escape_link.symlink_to(outside)
        # Asking to place "escape/trojan.py" into `inside` resolves, via
        # the symlink, to `outside/trojan.py` — outside the boundary.
        with pytest.raises(ValueError, match="Path traversal"):
            resolve_new_strategy_path(inside, "escape/trojan")


# ---------------------------------------------------------------------------
# Public facade: generate_scaffold end-to-end
# ---------------------------------------------------------------------------


class TestGenerateScaffold:

    def test_creates_file(self, tmp_path: Path):
        p = generate_scaffold("my_strat", tmp_path)
        assert p.exists()
        assert p.name == "my_strat.py"

    def test_content_is_parseable_python(self, tmp_path: Path):
        p = generate_scaffold("my_cool_strat", tmp_path)
        ast.parse(p.read_text())

    def test_class_and_config_names_correct(self, tmp_path: Path):
        p = generate_scaffold("my_scalper", tmp_path)
        content = p.read_text()
        assert "class MyScalper(" in content
        assert "class MyScalperConfig(" in content

    def test_strategies_dir_created_if_missing(self, tmp_path: Path):
        new_dir = tmp_path / "fresh" / "strategies"
        assert not new_dir.exists()
        generate_scaffold("x", new_dir)
        assert new_dir.is_dir()

    def test_rejects_existing_file(self, tmp_path: Path):
        generate_scaffold("dup", tmp_path)
        with pytest.raises(FileExistsError, match="already exists"):
            generate_scaffold("dup", tmp_path)

    def test_rejects_invalid_name(self, tmp_path: Path):
        with pytest.raises(ValueError, match="valid Python identifier"):
            generate_scaffold("bad-name", tmp_path)
        assert not (tmp_path / "bad-name.py").exists()

    def test_rejects_path_traversal_name(self, tmp_path: Path):
        # validate_identifier catches this before any path work.
        with pytest.raises(ValueError, match="valid Python identifier"):
            generate_scaffold("../evil", tmp_path)

    def test_returns_resolved_path(self, tmp_path: Path):
        p = generate_scaffold("x", tmp_path)
        assert p.is_absolute()

    def test_does_not_overwrite_unrelated_files(self, tmp_path: Path):
        other = tmp_path / "other.py"
        other.write_text("# untouched")
        generate_scaffold("new_strat", tmp_path)
        assert other.read_text() == "# untouched"

    def test_scaffold_type_accepted_but_no_effect(self, tmp_path: Path):
        """``scaffold_type`` is a reserved pass-through; behaviour identical."""
        p1 = generate_scaffold("as_strategy", tmp_path, scaffold_type="strategy")
        p2 = generate_scaffold(
            "as_portfolio", tmp_path / "pf", scaffold_type="portfolio"
        )
        # Two files with different names but equal-length templates (both go
        # through render_scaffold → STRATEGY_SCAFFOLD.format).
        a, b = p1.read_text(), p2.read_text()
        # Strip the rendered name/class to compare structural equality.
        assert a.replace("as_strategy", "X").replace("AsStrategy", "Y") == (
            b.replace("as_portfolio", "X").replace("AsPortfolio", "Y")
        )

    def test_arbitrary_scaffold_type_string_still_succeeds(self, tmp_path: Path):
        # Currently *any* string is accepted (forward-compat pass-through).
        p = generate_scaffold("foo", tmp_path, scaffold_type="whatever-you-want")
        assert p.exists()

    def test_accepts_pathlib_path_for_dir(self, tmp_path: Path):
        p = generate_scaffold("p1", tmp_path)
        assert p.exists()

    def test_accepts_str_for_dir(self, tmp_path: Path):
        p = generate_scaffold("p2", str(tmp_path))
        assert p.exists()


# ---------------------------------------------------------------------------
# Module-level surface
# ---------------------------------------------------------------------------


class TestModuleSurface:

    def test_strategy_scaffold_is_nonempty_string(self):
        assert isinstance(STRATEGY_SCAFFOLD, str)
        assert len(STRATEGY_SCAFFOLD) > 15_000

    def test_dead_aliases_removed(self):
        # 2026-04-21 cleanup: BAR_SCAFFOLD / TICK_SCAFFOLD were zero-use
        # aliases kept for historical compat. They are now gone; this test
        # pins that — if someone re-adds the dead code, the test flags it.
        assert not hasattr(scaffold, "BAR_SCAFFOLD")
        assert not hasattr(scaffold, "TICK_SCAFFOLD")

    def test_helpers_module_is_nt_free(self):
        """``scaffold_helpers`` must be importable without NautilusTrader."""
        # A quick delta check: importing scaffold_helpers does not pull NT.
        before = {m for m in sys.modules if m.startswith("nautilus_trader")}
        # Force a re-check (module already imported at file load time).
        import importlib

        import tinohelm.strategy.scaffold_helpers as sh

        importlib.reload(sh)
        after = {m for m in sys.modules if m.startswith("nautilus_trader")}
        # We should not have *introduced* any nautilus_trader modules by
        # reimporting the helpers. (Other tests in the same process may have
        # loaded NT already; that's why we compare deltas, not absolutes.)
        assert after.issubset(before)
