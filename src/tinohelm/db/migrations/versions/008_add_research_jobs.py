"""Add research_jobs table for persistent factor research jobs.

Revision ID: 008
Revises: 007
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(36), unique=True, nullable=False),
        sa.Column("factor_name", sa.String(100), nullable=False),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("data_type", sa.String(30), nullable=False, server_default="bar"),
        sa.Column("interval", sa.String(10), nullable=False, server_default="1m"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("parameters_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), server_default="0"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("result_path", sa.String(500), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("verdict_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_research_jobs_status", "research_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_research_jobs_status", table_name="research_jobs")
    op.drop_table("research_jobs")
