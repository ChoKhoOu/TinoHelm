"""Add data_fetch_jobs.started_at for running-age tracking.

The orphan sweeper must measure how long a job has actually been running,
not how long it sat queued before a worker claimed it. ``created_at`` is
queue-entry time; ``started_at`` is written when a worker flips a row to
``running`` and is used for stale-running detection.

Revision ID: 015
Revises: 014
"""

from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "data_fetch_jobs",
        sa.Column("started_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("data_fetch_jobs", "started_at")
