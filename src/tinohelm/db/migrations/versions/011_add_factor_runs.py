"""Add factor_runs table for declarative factor framework evaluation runs.

Stores EvalConfig snapshots, EvalResult outputs, progress, and code hash
for L2 cache association.

Revision ID: 011
Revises: 010
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_runs",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("factor_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("code_hash", sa.String(64), nullable=True),
    )
    op.create_index("ix_factor_runs_factor_name", "factor_runs", ["factor_name"])
    op.create_index("ix_factor_runs_status", "factor_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_factor_runs_status", table_name="factor_runs")
    op.drop_index("ix_factor_runs_factor_name", table_name="factor_runs")
    op.drop_table("factor_runs")
