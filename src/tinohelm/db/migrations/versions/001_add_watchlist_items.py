"""add watchlist_items table"""
from alembic import op
import sqlalchemy as sa

revision = "add_watchlist"
# Intentionally the first Alembic migration. Existing tables were created via
# Base.metadata.create_all during development; Alembic history starts here.
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.String(100), unique=True, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("watchlist_items")
