"""Widen symbol (to TEXT) and interval (to VARCHAR(50)) columns for multi-symbol portfolios.

Revision ID: 004
Revises: 003
Create Date: 2026-03-26
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("backtest_runs", "symbol", type_=sa.Text(), existing_type=sa.String(50), existing_nullable=False)
    op.alter_column("backtest_runs", "interval", type_=sa.String(50), existing_type=sa.String(10), existing_nullable=False)
    op.alter_column("optimization_runs", "symbol", type_=sa.Text(), existing_type=sa.String(50), existing_nullable=False)
    op.alter_column("optimization_runs", "interval", type_=sa.String(50), existing_type=sa.String(10), existing_nullable=False)


def downgrade() -> None:
    op.alter_column("backtest_runs", "symbol", type_=sa.String(50), existing_type=sa.Text(), existing_nullable=False)
    op.alter_column("backtest_runs", "interval", type_=sa.String(10), existing_type=sa.String(50), existing_nullable=False)
    op.alter_column("optimization_runs", "symbol", type_=sa.String(50), existing_type=sa.Text(), existing_nullable=False)
    op.alter_column("optimization_runs", "interval", type_=sa.String(10), existing_type=sa.String(50), existing_nullable=False)
