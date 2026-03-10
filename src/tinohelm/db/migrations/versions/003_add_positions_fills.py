"""Recreate positions and fills tables with richer schemas.

Revision ID: 003
Revises: 002
Create Date: 2026-03-10
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old tables (old schemas had fewer columns and different types)
    op.drop_table("fills")
    op.drop_table("positions")

    # Create new positions table
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("position_id", sa.String(100), nullable=False, unique=True),
        sa.Column("strategy_id_tag", sa.String(100), nullable=False),
        sa.Column("instrument_id", sa.String(100), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.String(50), nullable=False, server_default="0"),
        sa.Column("signed_qty", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_px_open", sa.Float(), nullable=True),
        sa.Column("avg_px_close", sa.Float(), nullable=True),
        sa.Column("realized_pnl", sa.Float(), nullable=True),
        sa.Column("unrealized_pnl", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(10), nullable=True),
        sa.Column("entry_side", sa.String(10), nullable=True),
        sa.Column("peak_qty", sa.String(50), nullable=True),
        sa.Column("ts_opened", sa.String(30), nullable=True),
        sa.Column("ts_closed", sa.String(30), nullable=True),
        sa.Column("duration", sa.String(50), nullable=True),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_positions_node_type", "positions", ["node_type"])
    op.create_index("ix_positions_instrument_id", "positions", ["instrument_id"])

    # Create new fills table
    op.create_table(
        "fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_type", sa.String(20), nullable=False),
        sa.Column("trade_id", sa.String(100), nullable=False, unique=True),
        sa.Column("position_id", sa.String(100), nullable=True),
        sa.Column("client_order_id", sa.String(100), nullable=False),
        sa.Column("venue_order_id", sa.String(100), nullable=True),
        sa.Column("strategy_id_tag", sa.String(100), nullable=True),
        sa.Column("instrument_id", sa.String(100), nullable=False),
        sa.Column("order_side", sa.String(10), nullable=False),
        sa.Column("last_qty", sa.String(50), nullable=False),
        sa.Column("last_px", sa.String(50), nullable=False),
        sa.Column("commission", sa.String(50), nullable=True),
        sa.Column("liquidity_side", sa.String(10), nullable=True),
        sa.Column("ts_event", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_fills_node_type", "fills", ["node_type"])
    op.create_index("ix_fills_position_id", "fills", ["position_id"])
    op.create_index("ix_fills_instrument_id", "fills", ["instrument_id"])


def downgrade() -> None:
    # Drop new tables
    op.drop_index("ix_fills_instrument_id", table_name="fills")
    op.drop_index("ix_fills_position_id", table_name="fills")
    op.drop_index("ix_fills_node_type", table_name="fills")
    op.drop_table("fills")

    op.drop_index("ix_positions_instrument_id", table_name="positions")
    op.drop_index("ix_positions_node_type", table_name="positions")
    op.drop_table("positions")

    # Recreate old tables
    node_type_enum = sa.Enum("backtest", "sandbox", "live", name="nodetype")

    op.create_table(
        "fills",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("node_type", node_type_enum, nullable=False),
        sa.Column("order_id", sa.String(100), nullable=False),
        sa.Column("instrument_id", sa.String(100), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.String(30), nullable=False),
        sa.Column("price", sa.String(30), nullable=False),
        sa.Column("commission", sa.String(30), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("node_type", node_type_enum, nullable=False),
        sa.Column("instrument_id", sa.String(100), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.String(30), nullable=False),
        sa.Column("avg_price", sa.String(30), nullable=False),
        sa.Column("unrealized_pnl", sa.String(30), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.String(30), nullable=False, server_default="0"),
        sa.Column("strategy_id", sa.String(100), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_positions_node_instrument", "positions", ["node_type", "instrument_id"])
