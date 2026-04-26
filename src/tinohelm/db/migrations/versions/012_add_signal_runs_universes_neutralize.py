"""Add signal_runs/universes tables + factor_runs neutralization columns.

Creates universes (universe-as-first-class-object), signal_runs (signal evaluation
runs mirroring factor_runs pattern), exposures_cache (btc_beta / log_mcap provider
cache), and extends factor_runs with 7 new nullable columns for walk-forward,
neutralization, segmentation, and progress-stage tracking.

Revision ID: 012
Revises: 011
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. universes 表（一等对象）──
    op.create_table(
        "universes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("source_csv_path", sa.String(500), nullable=False),
        sa.Column("source_csv_hash", sa.String(64), nullable=False),
        sa.Column("min_history_bars", sa.Integer, nullable=False, server_default="100"),
        sa.Column("new_coin_isolation_days", sa.Integer, nullable=False, server_default="7"),
        sa.Column("pit_rules_json", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_universes_name", "universes", ["name"], unique=True)

    # ── 2. factor_runs 7 新列 ──
    op.add_column("factor_runs", sa.Column("baseline_id", sa.String(36), nullable=True))
    op.add_column("factor_runs", sa.Column("oos_ic_series", sa.JSON, nullable=True))
    op.add_column("factor_runs", sa.Column("neutralization_config", sa.JSON, nullable=True))
    op.add_column("factor_runs", sa.Column("universe_id", sa.Integer, nullable=True))
    op.add_column("factor_runs", sa.Column("signal_spec_id", sa.String(36), nullable=True))
    op.add_column("factor_runs", sa.Column("segment_results", sa.JSON, nullable=True))
    # 4-stage progress: aligning / computing / evaluating / persisting
    op.add_column("factor_runs", sa.Column("progress_stage", sa.String(40), nullable=True))
    op.create_foreign_key(
        "fk_factor_runs_universe", "factor_runs", "universes",
        ["universe_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_factor_runs_baseline", "factor_runs", ["baseline_id"])
    op.create_index("ix_factor_runs_universe", "factor_runs", ["universe_id"])

    # ── 3. signal_runs 表 ──
    op.create_table(
        "signal_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("signal_name", sa.String(255), nullable=False),
        sa.Column("factor_ref", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON, nullable=True),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("progress_stage", sa.String(40), nullable=True),  # aligning/computing/evaluating/persisting
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("code_hash", sa.String(64), nullable=True),
        sa.Column("universe_id", sa.Integer, nullable=True),
    )
    op.create_index("ix_signal_runs_signal_name", "signal_runs", ["signal_name"])
    op.create_index("ix_signal_runs_factor_ref", "signal_runs", ["factor_ref"])
    op.create_index("ix_signal_runs_status", "signal_runs", ["status"])
    op.create_foreign_key(
        "fk_signal_runs_universe", "signal_runs", "universes",
        ["universe_id"], ["id"], ondelete="SET NULL",
    )

    # ── 4. exposures_cache 表 ──
    op.create_table(
        "exposures_cache",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider_name", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("ts_event_ns", sa.BigInteger, nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("computed_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_exposures_cache_lookup",
        "exposures_cache",
        ["provider_name", "symbol", "ts_event_ns"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_exposures_cache_lookup", table_name="exposures_cache")
    op.drop_table("exposures_cache")

    op.drop_constraint("fk_signal_runs_universe", "signal_runs", type_="foreignkey")
    op.drop_index("ix_signal_runs_status", table_name="signal_runs")
    op.drop_index("ix_signal_runs_factor_ref", table_name="signal_runs")
    op.drop_index("ix_signal_runs_signal_name", table_name="signal_runs")
    op.drop_table("signal_runs")

    op.drop_index("ix_factor_runs_universe", table_name="factor_runs")
    op.drop_index("ix_factor_runs_baseline", table_name="factor_runs")
    op.drop_constraint("fk_factor_runs_universe", "factor_runs", type_="foreignkey")
    for col in (
        "progress_stage", "segment_results", "signal_spec_id", "universe_id",
        "neutralization_config", "oos_ic_series", "baseline_id",
    ):
        op.drop_column("factor_runs", col)

    op.drop_index("ix_universes_name", table_name="universes")
    op.drop_table("universes")
