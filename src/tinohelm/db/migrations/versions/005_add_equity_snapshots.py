"""Add equity_snapshots table for trading dashboard equity curve.

Revision ID: 005
Revises: 004
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equity_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("equity", sa.Float(), nullable=False),
        sa.Column("balance", sa.Float(), nullable=False),
        sa.Column("unrealized_pnl", sa.Float(), nullable=False),
        sa.Column("ts", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_equity_snapshots_node_ts", "equity_snapshots", ["node_type", "ts"])


def downgrade() -> None:
    op.drop_index("ix_equity_snapshots_node_ts", table_name="equity_snapshots")
    op.drop_table("equity_snapshots")
