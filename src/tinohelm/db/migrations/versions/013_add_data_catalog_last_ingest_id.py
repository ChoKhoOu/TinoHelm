"""Add data_catalog last_ingest_id commit witness.

Revision ID: 013
Revises: 012
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("data_catalog", sa.Column("last_ingest_id", sa.String(36), nullable=True))


def downgrade() -> None:
    op.drop_column("data_catalog", "last_ingest_id")
