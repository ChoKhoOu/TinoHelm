"""Add data_fetch_jobs table for persistent data ingestion jobs.

Revision ID: 007
Revises: 006
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_fetch_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(36), unique=True, nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("data_type", sa.String(30), nullable=False),
        sa.Column("interval", sa.String(10), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("asset_class", sa.String(5), nullable=False, server_default="um"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_data_fetch_jobs_status", "data_fetch_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_data_fetch_jobs_status", table_name="data_fetch_jobs")
    op.drop_table("data_fetch_jobs")
