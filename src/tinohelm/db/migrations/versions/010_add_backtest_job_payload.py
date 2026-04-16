"""Add job_payload_json column to backtest_runs for restart recovery.

Stores the full Redis queue payload so interrupted runs can be re-enqueued
on API restart without losing fill_model, fees, warmup_bars, or tags.

Revision ID: 010
Revises: 009
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backtest_runs", sa.Column("job_payload_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_runs", "job_payload_json")
