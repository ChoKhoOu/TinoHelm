"""SQLAlchemy ORM models for TinoHelm."""
from __future__ import annotations

import enum
from datetime import datetime, date
from uuid import uuid4

from sqlalchemy import (
    Boolean, String, Integer, Float, Text, DateTime, Date, Enum, ForeignKey,
    JSON, BigInteger, Index, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NodeType(str, enum.Enum):
    backtest = "backtest"
    sandbox = "sandbox"
    live = "live"


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class StrategyType(str, enum.Enum):
    single = "single"
    portfolio = "portfolio"  # Deprecated: kept for DB backward compat, no longer generated


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    strategy_class: Mapped[str] = mapped_column(String(255), nullable=False)
    config_class: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[StrategyType] = mapped_column(
        Enum(StrategyType), default=StrategyType.single, nullable=False,
        server_default="single",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    versions: Mapped[list[StrategyVersion]] = relationship(back_populates="strategy", cascade="all, delete-orphan")


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    strategy: Mapped[Strategy] = relationship(back_populates="versions")

    __table_args__ = (
        Index("ix_strategy_version", "strategy_id", "version", unique=True),
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=lambda: str(uuid4()))
    strategy_name: Mapped[str] = mapped_column(String(255), nullable=False, server_default="")
    strategy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # legacy, no FK
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    params_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued, nullable=False)
    result_summary_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OptimizationStatus(str, enum.Enum):
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class OptimizationRun(Base):
    __tablename__ = "optimization_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    interval: Mapped[str] = mapped_column(String(50), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    n_trials: Mapped[int] = mapped_column(Integer, nullable=False)
    fitness_objective: Mapped[str] = mapped_column(String(30), nullable=False)
    train_pct: Mapped[float] = mapped_column(Float, nullable=False, default=85.0)
    status: Mapped[OptimizationStatus] = mapped_column(
        Enum(OptimizationStatus), default=OptimizationStatus.running,
    )
    best_params_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    best_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    trials_completed: Mapped[int] = mapped_column(Integer, default=0)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_type: Mapped[NodeType] = mapped_column(Enum(NodeType), nullable=False)
    order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[str] = mapped_column(String(30), nullable=False)
    price: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_orders_node_instrument", "node_type", "instrument_id"),
    )


class Fill(Base):
    """Immutable fill/trade record from live/sandbox trading."""
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    position_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    venue_order_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    strategy_id_tag: Mapped[str | None] = mapped_column(String(100), nullable=True)
    instrument_id: Mapped[str] = mapped_column(String(100), nullable=False)
    order_side: Mapped[str] = mapped_column(String(10), nullable=False)
    last_qty: Mapped[str] = mapped_column(String(50), nullable=False)
    last_px: Mapped[str] = mapped_column(String(50), nullable=False)
    commission: Mapped[str | None] = mapped_column(String(50), nullable=True)
    liquidity_side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ts_event: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_fills_node_type", "node_type"),
        Index("ix_fills_position_id", "position_id"),
        Index("ix_fills_instrument_id", "instrument_id"),
    )


class Position(Base):
    """Live/sandbox position snapshot -- upserted on each position event."""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    position_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    strategy_id_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    instrument_id: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[str] = mapped_column(String(50), nullable=False, server_default="0")
    signed_qty: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    avg_px_open: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_px_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    entry_side: Mapped[str | None] = mapped_column(String(10), nullable=True)
    peak_qty: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ts_opened: Mapped[str | None] = mapped_column(String(30), nullable=True)
    ts_closed: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duration: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_positions_node_type", "node_type"),
        Index("ix_positions_instrument_id", "instrument_id"),
    )


class EquitySnapshot(Base):
    """Periodic equity snapshot for equity curve visualization."""
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    ts: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_equity_snapshots_node_ts", "node_type", "ts"),
    )


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_type: Mapped[NodeType] = mapped_column(Enum(NodeType), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DataCatalog(Base):
    __tablename__ = "data_catalog"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    data_type: Mapped[str] = mapped_column(String(30), nullable=False)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    record_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    last_ingest_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_data_catalog_symbol_interval", "symbol", "data_type", "interval"),
    )


class DataFetchJob(Base):
    """Persistent data-fetch job — survives API restarts."""
    __tablename__ = "data_fetch_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, default=lambda: str(uuid4()))
    symbol: Mapped[str] = mapped_column(String(50), nullable=False)
    data_type: Mapped[str] = mapped_column(String(30), nullable=False)
    interval: Mapped[str | None] = mapped_column(String(10), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    asset_class: Mapped[str] = mapped_column(String(5), nullable=False, server_default="um")
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_data_fetch_jobs_status", "status"),
    )


class FactorRun(Base):
    """Factor evaluation run — stores EvalConfig snapshot, EvalResult, and progress."""
    __tablename__ = "factor_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    factor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued", nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ── 012 new columns ──
    baseline_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    oos_ic_series: Mapped[list | None] = mapped_column(JSON, nullable=True)
    neutralization_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    universe_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("universes.id", ondelete="SET NULL"), nullable=True)
    signal_spec_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    segment_results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)  # aligning/computing/evaluating/persisting

    universe: Mapped["Universe | None"] = relationship("Universe", back_populates="factor_runs", foreign_keys="[FactorRun.universe_id]")

    __table_args__ = (
        Index("ix_factor_runs_factor_name", "factor_name"),
        Index("ix_factor_runs_status", "status"),
        Index("ix_factor_runs_baseline", "baseline_id"),
        Index("ix_factor_runs_universe", "universe_id"),
    )


class SignalRun(Base):
    """Signal evaluation run — mirrors factor_runs pattern for signal kernel evaluation."""
    __tablename__ = "signal_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    signal_name: Mapped[str] = mapped_column(String(255), nullable=False)
    factor_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued", nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    progress_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)  # aligning/computing/evaluating/persisting
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    universe_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("universes.id", ondelete="SET NULL"), nullable=True)

    universe: Mapped["Universe | None"] = relationship("Universe", back_populates="signal_runs", foreign_keys="[SignalRun.universe_id]")

    __table_args__ = (
        Index("ix_signal_runs_signal_name", "signal_name"),
        Index("ix_signal_runs_factor_ref", "factor_ref"),
        Index("ix_signal_runs_status", "status"),
    )


class Universe(Base):
    """Universe-as-first-class-object — PIT-aware symbol list for factor/signal evaluation."""
    __tablename__ = "universes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    source_csv_path: Mapped[str] = mapped_column(String(500), nullable=False)
    source_csv_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    min_history_bars: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    new_coin_isolation_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    pit_rules_json: Mapped[dict] = mapped_column(JSON, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    factor_runs: Mapped[list["FactorRun"]] = relationship("FactorRun", back_populates="universe", foreign_keys="[FactorRun.universe_id]")
    signal_runs: Mapped[list["SignalRun"]] = relationship("SignalRun", back_populates="universe", foreign_keys="[SignalRun.universe_id]")

    __table_args__ = (
        Index("ix_universes_name", "name", unique=True),
    )


class ExposuresCache(Base):
    """Cached exposure values for btc_beta / log_mcap providers."""
    __tablename__ = "exposures_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    ts_event_ns: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_exposures_cache_lookup", "provider_name", "symbol", "ts_event_ns", unique=True),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
