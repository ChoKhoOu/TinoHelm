"""Add data_fetch_jobs batch finalize witness fields.

FetchBatch-level consolidation/finalize now happens after all jobs in the
batch reach terminal state. These nullable columns persist the finalize phase
state on the batch's member rows so recovery / polling can distinguish:
- terminal ingest complete but finalize not started
- finalize currently in progress
- finalize completed successfully
- finalize failed and needs retry/visibility

Revision ID: 016
Revises: 015
"""

from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_fetch_jobs",
        sa.Column("batch_finalize_started_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "data_fetch_jobs",
        sa.Column("batch_finalized_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "data_fetch_jobs",
        sa.Column("batch_finalize_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_fetch_jobs", "batch_finalize_error")
    op.drop_column("data_fetch_jobs", "batch_finalized_at")
    op.drop_column("data_fetch_jobs", "batch_finalize_started_at")
