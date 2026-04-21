"""Tests for pure helpers in tinohelm.api.routes.dashboard."""
from __future__ import annotations

from tinohelm.api.routes.dashboard import _completed_runs_stmt
from tinohelm.db.models import BacktestRun, RunStatus


class TestCompletedRunsStmt:
    def test_filters_by_completed_status(self):
        """SQL compiles and contains the status predicate."""
        stmt = _completed_runs_stmt()
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql_text = str(compiled)

        # The selectable should be the BacktestRun table, not Strategy etc.
        assert BacktestRun.__tablename__ in sql_text
        # And include the status predicate
        assert "status" in sql_text.lower()

    def test_stmt_is_builder_not_frozen(self):
        """Caller can further chain .where(), .order_by(), etc."""
        stmt = _completed_runs_stmt()
        chained = stmt.where(BacktestRun.id > 0).limit(10)
        compiled = chained.compile(compile_kwargs={"literal_binds": True})
        sql_text = str(compiled)
        assert "LIMIT 10" in sql_text

    def test_completed_status_value(self):
        """Lock the contract: the base query filters on RunStatus.completed."""
        assert RunStatus.completed.value == "completed"
        stmt = _completed_runs_stmt()
        compiled = stmt.compile(compile_kwargs={"literal_binds": True})
        sql_text = str(compiled)
        assert "completed" in sql_text.lower()
