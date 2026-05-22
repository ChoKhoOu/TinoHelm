"""Add data_fetch_jobs.batch_id FetchBatch identity.

One user-submitted fetch-batch = one FetchBatch. All DataFetchJob rows
created by that submission share one batch_id. Backtest-triggered
standalone fetches form single-job FetchBatch instances.

Column is nullable so pre-existing rows stay valid; the application
layer (API route + backtest runner) always populates it for new jobs.
Historical backlog migration (grouping by created_at) is a separate
slice (see PRD #162 decisions #23-#24).

Revision ID: 014
Revises: 013
"""

from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_fetch_jobs",
        sa.Column("batch_id", sa.String(36), nullable=True),
    )
    op.create_index(
        "ix_data_fetch_jobs_batch_id",
        "data_fetch_jobs",
        ["batch_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_data_fetch_jobs_batch_id", table_name="data_fetch_jobs")
    op.drop_column("data_fetch_jobs", "batch_id")
