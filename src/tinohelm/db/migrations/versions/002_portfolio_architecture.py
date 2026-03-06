"""Portfolio architecture: add strategy_name, type, drop FK.

Revision ID: 002
Revises: add_watchlist
Create Date: 2026-03-05
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "add_watchlist"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add 'type' column to strategies table
    op.add_column(
        "strategies",
        sa.Column("type", sa.String(20), nullable=False, server_default="single"),
    )

    # 2. Add 'strategy_name' column to backtest_runs
    op.add_column(
        "backtest_runs",
        sa.Column("strategy_name", sa.String(255), nullable=False, server_default=""),
    )

    # 3. Backfill strategy_name from joined strategy name
    op.execute(
        """
        UPDATE backtest_runs
        SET strategy_name = (
            SELECT strategies.name
            FROM strategies
            WHERE strategies.id = backtest_runs.strategy_id
        )
        WHERE strategy_name = '' AND strategy_id IS NOT NULL
        """
    )

    # 4. Drop FK constraint from backtest_runs.strategy_id
    # Note: constraint name may vary by DB; use batch mode for SQLite compat
    with op.batch_alter_table("backtest_runs") as batch_op:
        batch_op.drop_constraint(
            "backtest_runs_strategy_id_fkey", type_="foreignkey"
        )
        batch_op.alter_column("strategy_id", nullable=True)

    # 5. Add index on strategy_name for query performance
    op.create_index("ix_backtest_runs_strategy_name", "backtest_runs", ["strategy_name"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_strategy_name", table_name="backtest_runs")

    with op.batch_alter_table("backtest_runs") as batch_op:
        batch_op.alter_column("strategy_id", nullable=False)
        batch_op.create_foreign_key(
            "backtest_runs_strategy_id_fkey",
            "strategies",
            ["strategy_id"],
            ["id"],
        )

    op.drop_column("backtest_runs", "strategy_name")
    op.drop_column("strategies", "type")
