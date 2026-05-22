"""Add record_count and source_type columns to data_catalog.

Revision ID: 006
Revises: 005
Create Date: 2026-04-05
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_catalog", sa.Column("record_count", sa.BigInteger(), nullable=True))
    op.add_column("data_catalog", sa.Column("source_type", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("data_catalog", "source_type")
    op.drop_column("data_catalog", "record_count")
