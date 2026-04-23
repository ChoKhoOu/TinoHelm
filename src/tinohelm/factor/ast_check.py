"""AST-based static analysis for factor functions.

``ShiftDetector`` scans a factor function's source code for ``Series.shift(N)``
calls and computes the cumulative lookback implied by those calls.

Design notes
------------
- Only *literal* integer arguments are analyzed.  Variable arguments (e.g.
  ``shift(n)`` where ``n`` is a name) conservatively contribute 0 and emit a
  warning.
- Chained shifts such as ``close.shift(-3).shift(-2)`` are summed: the
  detector treats each `.shift()` call independently and returns the total
  absolute shift accumulated along any single chain.
- The detector works on the raw AST produced by ``ast.parse``, which means it
  handles nested functions defined in the body but ignores lambdas that cannot
  be retrieved via ``inspect.getsource`` (those are silently skipped upstream
  via the decorator's try/except wrapper).
"""
from __future__ import annotations

import ast
import inspect
import logging
import textwrap
from typing import Callable

logger = logging.getLogger(__name__)


class ShiftDetector(ast.NodeVisitor):
    """Visitor that accumulates absolute shift amounts from ``.shift(N)`` calls.

    The visitor traverses the AST and identifies ``ast.Call`` nodes that
    represent attribute calls named ``shift`` with a single argument.

    For chained expressions such as ``a.shift(-3).shift(-2)``, the detector
    visits all ``Call`` nodes in the tree independently — each qualifying call
    contributes its absolute value to the running total.  The public entry
    point :meth:`detect_max_shift` returns the maximum *per-chain* accumulated
    value (see implementation note below).

    Attributes
    ----------
    _shifts:
        List of absolute shift amounts found during the visit.  Each entry
        corresponds to one qualifying ``.shift(N)`` call with a literal integer
        argument.
    _has_dynamic:
        Set to ``True`` if a ``.shift(expr)`` call with a non-literal argument
        is encountered.
    """

    def __init__(self) -> None:
        self._shifts: list[int] = []
        self._has_dynamic: bool = False

    # ------------------------------------------------------------------
    # Public class-level entry point
    # ------------------------------------------------------------------

    @classmethod
    def detect_max_shift(cls, func: Callable) -> int:  # type: ignore[type-arg]
        """Return the maximum cumulative lookback implied by ``shift`` calls.

        The method:

        1. Retrieves the function source via ``inspect.getsource``.
        2. Parses the source with ``ast.parse``.
        3. Visits all ``Call`` nodes to collect ``.shift(N)`` literals.
        4. Returns the sum of all absolute shift values found (conservative
           upper bound: assumes all shifts apply along the same chain/path).

        If ``inspect.getsource`` fails (e.g. built-ins, C extensions, or
        interactive-session closures), the method logs a warning and returns
        ``0``.

        Parameters
        ----------
        func:
            The factor function to analyze.

        Returns
        -------
        int
            The total absolute shift lookback detected.  ``0`` if no
            qualifying ``shift`` calls are found or if source is unavailable.
        """
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError):
            logger.warning(
                "ShiftDetector: cannot retrieve source for %r — shift analysis skipped",
                getattr(func, "__name__", func),
            )
            return 0

        # ``inspect.getsource`` may return indented source for methods/nested
        # functions.  ``textwrap.dedent`` normalises indentation so
        # ``ast.parse`` doesn't raise ``IndentationError``.
        source = textwrap.dedent(source)

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            logger.warning(
                "ShiftDetector: AST parse failed for %r: %s — shift analysis skipped",
                getattr(func, "__name__", func),
                exc,
            )
            return 0

        detector = cls()
        detector.visit(tree)

        if detector._has_dynamic:
            logger.warning(
                "ShiftDetector: %r contains .shift(expr) with a non-literal argument; "
                "dynamic shifts contribute 0 to lookback (conservative estimate)",
                getattr(func, "__name__", func),
            )

        return sum(detector._shifts)

    # ------------------------------------------------------------------
    # AST visitor
    # ------------------------------------------------------------------

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        """Handle every function-call node in the AST.

        Qualifies a call as a ``shift`` call when:
        - It is an attribute access (``ast.Attribute`` as ``func``).
        - The attribute name is ``"shift"``.
        - Exactly one positional argument is provided (no kwargs).

        The argument is then inspected:
        - ``ast.Constant`` with an ``int`` value → ``abs(value)`` appended
          to ``_shifts``.
        - ``ast.UnaryOp`` with ``ast.USub`` and an ``ast.Constant`` int →
          treated as negative integer literal; ``abs(value)`` appended.
        - Any other expression → ``_has_dynamic`` set to ``True`` (contributes
          0 conservatively).
        """
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "shift"
            and len(node.args) == 1
            and not node.keywords
        ):
            arg = node.args[0]
            shift_value = self._extract_int_literal(arg)
            if shift_value is not None:
                self._shifts.append(abs(shift_value))
            else:
                self._has_dynamic = True

        # Always recurse into child nodes
        self.generic_visit(node)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_int_literal(node: ast.expr) -> int | None:
        """Extract an integer literal from an AST expression node.

        Handles:
        - ``ast.Constant`` with ``int`` value (positive literal, e.g. ``3``)
        - ``ast.UnaryOp(USub, Constant(int))`` (negative literal, e.g. ``-3``)

        Returns ``None`` for any other node type (variables, calls, etc.).
        """
        # Positive literal: shift(3)
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value

        # Negative literal: shift(-3)
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
            and isinstance(node.operand.value, int)
        ):
            return -node.operand.value

        return None
