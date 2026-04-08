"""Migrate data_catalog file_path and source_type for new catalog structure.

bar entries: file_path → {catalog}/bar/klines, source_type → 'klines'
trade_tick entries: file_path → {catalog}/ticks/aggTrades, source_type → 'aggTrades'

Revision ID: 009
Revises: 008
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Get distinct file_path values used by bar entries (should be the base catalog path)
    rows = conn.execute(
        sa.text("SELECT DISTINCT file_path FROM data_catalog WHERE data_type = 'bar'")
    ).fetchall()
    for (old_path,) in rows:
        new_path = old_path.rstrip("/") + "/bar/klines"
        conn.execute(
            sa.text(
                "UPDATE data_catalog SET file_path = :new_path, source_type = 'klines' "
                "WHERE data_type = 'bar' AND file_path = :old_path"
            ),
            {"new_path": new_path, "old_path": old_path},
        )

    # Get distinct file_path values used by trade_tick entries
    rows = conn.execute(
        sa.text("SELECT DISTINCT file_path FROM data_catalog WHERE data_type = 'trade_tick'")
    ).fetchall()
    for (old_path,) in rows:
        new_path = old_path.rstrip("/") + "/ticks/aggTrades"
        conn.execute(
            sa.text(
                "UPDATE data_catalog SET file_path = :new_path, source_type = 'aggTrades' "
                "WHERE data_type = 'trade_tick' AND file_path = :old_path"
            ),
            {"new_path": new_path, "old_path": old_path},
        )


def downgrade() -> None:
    conn = op.get_bind()

    # Reverse: strip /bar/klines suffix
    rows = conn.execute(
        sa.text("SELECT DISTINCT file_path FROM data_catalog WHERE data_type = 'bar' AND source_type = 'klines'")
    ).fetchall()
    for (cur_path,) in rows:
        if cur_path.endswith("/bar/klines"):
            old_path = cur_path[: -len("/bar/klines")]
            conn.execute(
                sa.text(
                    "UPDATE data_catalog SET file_path = :old_path, source_type = NULL "
                    "WHERE data_type = 'bar' AND file_path = :cur_path"
                ),
                {"old_path": old_path, "cur_path": cur_path},
            )

    # Reverse: strip /ticks/aggTrades suffix
    rows = conn.execute(
        sa.text("SELECT DISTINCT file_path FROM data_catalog WHERE data_type = 'trade_tick' AND source_type = 'aggTrades'")
    ).fetchall()
    for (cur_path,) in rows:
        if cur_path.endswith("/ticks/aggTrades"):
            old_path = cur_path[: -len("/ticks/aggTrades")]
            conn.execute(
                sa.text(
                    "UPDATE data_catalog SET file_path = :old_path, source_type = NULL "
                    "WHERE data_type = 'trade_tick' AND file_path = :cur_path"
                ),
                {"old_path": old_path, "cur_path": cur_path},
            )
