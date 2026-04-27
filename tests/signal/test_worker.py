"""Unit tests for :mod:`tinohelm.signal.worker`.

All tests use mocked Redis and DB sessions — no real I/O is performed.

Coverage
--------
- AC-3.5.1: 4-stage progress events with ``stage`` field and DB
  ``progress_stage`` UPDATEs in the canonical order
  ``aligning → computing → evaluating → persisting``.
- AC-5.1.1 / AC-5.2.1: cancel flag honoured between stages; failed
  jobs land in ``status="failed"`` with traceback persisted.
- Recovery: ``recover_interrupted_jobs`` flips running → queued.
- ``start_signal_worker`` / ``stop_signal_worker`` lifecycle.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import polars as pl
import pytest

from tinohelm.signal.evaluator import SignalEvalResult
from tinohelm.signal.types import CostModel
from tinohelm.signal.worker import (
    STAGE_ALIGNING,
    STAGE_COMPUTING,
    STAGE_EVALUATING,
    STAGE_PERSISTING,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_payload(
    run_id: str = "test-sig-run-1",
    signal_name: str = "test_signal",
    config: dict | None = None,
) -> str:
    return json.dumps({
        "run_id": run_id,
        "signal_name": signal_name,
        "config": config or _default_config(),
    })


def _default_config() -> dict:
    return {
        "factor_ref": "ret_N@1.0.0",
        "method": "top_k_long_short",
        "weighting": "equal",
        "rebalance_freq": "1D",
        "universe_ref": "top10_perp",
        "gross_exposure": 1.0,
        "net_exposure": 0.0,
        "max_position": 0.5,
        "method_params": {"k": 1},
        "cost_model": {
            "name": "taker_8bps",
            "fee_bps_per_side": 4.0,
            "slippage_bps_per_side": 1.0,
            "rebate_bps_per_side": 0.0,
        },
        "extra_warmup_bars": 0,
        "version": "1.0.0",
        "code_hash": "",
        "description": "",
        "deprecated": False,
        "periods_per_year": 365 * 24,
    }


def _make_mock_run(
    run_id: str = "test-sig-run-1",
    signal_name: str = "test_signal",
    status: str = "queued",
    config: dict | None = None,
) -> MagicMock:
    run = MagicMock()
    run.id = run_id
    run.signal_name = signal_name
    run.status = status
    run.config = config or _default_config()
    return run


def _sample_panels() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Synthetic factor + future-returns panels for kernel/eval pipeline.

    4 timestamps × 4 symbols.  The factor row order is monotone enough
    that ``top_k_long_short(k=1)`` produces deterministic weights.
    """
    ts = pl.Series(
        "ts",
        [dt.datetime(2024, 1, 1, h) for h in range(4)],
    )
    factor = pl.DataFrame({
        "ts": ts,
        "S00": [0.5, 0.6, 0.4, 0.3],
        "S01": [-0.5, 0.1, 0.7, 0.8],
        "S02": [0.0, -0.3, -0.2, 0.5],
        "S03": [-0.7, -0.6, -0.5, -0.4],
    })
    rng = np.random.default_rng(42)
    returns_data = rng.standard_normal((4, 4)) * 0.01
    returns = pl.DataFrame({
        "ts": ts,
        "S00": returns_data[:, 0].tolist(),
        "S01": returns_data[:, 1].tolist(),
        "S02": returns_data[:, 2].tolist(),
        "S03": returns_data[:, 3].tolist(),
    })
    return factor, returns


@pytest.fixture
def mock_rds():
    """Async-mock Redis client.  Cancel flag is OFF by default."""
    rds = AsyncMock()
    rds.exists.return_value = 0
    rds.publish = AsyncMock()
    rds.setex = AsyncMock()
    rds.close = AsyncMock()
    return rds


@pytest.fixture
def captured_session_factory():
    """Session factory that records every UPDATE-clause it receives.

    Each call to ``session.execute`` snapshots the compiled SQL parameters
    so tests can assert which fields (status / progress_stage / progress)
    were SET in which order.

    Statements are classified into UPDATE vs SELECT by inspecting
    SQLAlchemy's compiled visit name — UPDATEs return a ``rowcount`` mock
    and are appended to ``captured``; SELECTs return the seeded run row.
    """
    db_run = _make_mock_run()

    captured: list[dict] = []

    async def _execute(stmt):
        try:
            compiled = stmt.compile()
            params = dict(compiled.params)
        except Exception:
            params = {}
        # ``Update`` and ``Select`` core constructs expose a stable
        # ``__visit_name__`` attribute; we use it directly so we never
        # mis-classify a statement.
        visit = getattr(stmt, "__visit_name__", "")
        if visit == "update":
            captured.append(params)
            return MagicMock(rowcount=1)
        # SELECT — return the seeded run row.
        return MagicMock(scalar_one_or_none=MagicMock(return_value=db_run))

    session = AsyncMock()
    session.execute = _execute
    session.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, session, captured, db_run


@pytest.fixture
def fake_eval_result():
    return SignalEvalResult(
        sharpe=1.5,
        mdd=0.07,
        turnover_annualized=12.0,
        capacity_score=0.75,
        tail_loss_p99=-0.02,
        net_pnl_curve=[0.01, 0.02, 0.015, 0.03],
        gross_pnl_curve=[0.012, 0.024, 0.020, 0.035],
        total_return=0.03,
        n_periods=4,
        cost_drag=0.005,
    )


# ---------------------------------------------------------------------------
# AC-3.5.1: 4 stages emit progress events with ``stage`` field
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_progress_events_emit_4_stages_in_order(
    mock_rds, captured_session_factory, fake_eval_result
):
    """Worker must emit exactly 4 progress events tagged with the canonical stage."""
    factory, _session, _captured, _run = captured_session_factory
    factor, returns = _sample_panels()

    with (
        patch("tinohelm.signal.worker.get_session_factory", return_value=factory),
        patch("tinohelm.signal.worker.aioredis.from_url", return_value=mock_rds),
        patch(
            "tinohelm.signal.worker._load_aligned_panels",
            return_value=(factor, returns),
        ),
        patch.object(
            __import__("tinohelm.signal.worker", fromlist=["SignalEvaluator"]).SignalEvaluator,
            "evaluate",
            return_value=fake_eval_result,
        ),
    ):
        from tinohelm.signal.worker import _process_job
        await _process_job(_make_payload(), "redis://localhost:6379")

    # Inspect every progress publish — filter by channel.
    progress_payloads = []
    for c in mock_rds.publish.call_args_list:
        ch, body = c.args[0], c.args[1]
        if ch.startswith("tino:signal:progress:"):
            progress_payloads.append(json.loads(body))

    stages_in_order = [p["stage"] for p in progress_payloads if p.get("stage")]
    assert stages_in_order == [
        STAGE_ALIGNING,
        STAGE_COMPUTING,
        STAGE_EVALUATING,
        STAGE_PERSISTING,
    ], f"expected 4 stages in canonical order, got: {stages_in_order}"


# ---------------------------------------------------------------------------
# AC-3.5.1: progress_stage column UPDATEd at every stage boundary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_progress_stage_persisted_to_db_in_order(
    mock_rds, captured_session_factory, fake_eval_result
):
    """progress_stage column must be SET to each of the 4 stage values."""
    factory, _session, captured, _run = captured_session_factory
    factor, returns = _sample_panels()

    with (
        patch("tinohelm.signal.worker.get_session_factory", return_value=factory),
        patch("tinohelm.signal.worker.aioredis.from_url", return_value=mock_rds),
        patch(
            "tinohelm.signal.worker._load_aligned_panels",
            return_value=(factor, returns),
        ),
        patch.object(
            __import__("tinohelm.signal.worker", fromlist=["SignalEvaluator"]).SignalEvaluator,
            "evaluate",
            return_value=fake_eval_result,
        ),
    ):
        from tinohelm.signal.worker import _process_job
        await _process_job(_make_payload(), "redis://localhost:6379")

    # Walk the captured UPDATE params, pull the progress_stage values.
    stage_values_in_order = [
        p["progress_stage"]
        for p in captured
        if "progress_stage" in p and p.get("progress_stage") is not None
    ]
    assert stage_values_in_order == [
        STAGE_ALIGNING,
        STAGE_COMPUTING,
        STAGE_EVALUATING,
        STAGE_PERSISTING,
    ], f"expected 4 progress_stage UPDATEs, got: {stage_values_in_order}"


# ---------------------------------------------------------------------------
# AC-5.2.1: cancel flag breaks at next progress check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_flag_pre_load_skips_job(captured_session_factory):
    """SET cancel before run_id is loaded → status flips to cancelled, no work."""
    factory, _session, captured, _run = captured_session_factory

    rds = AsyncMock()
    rds.exists.return_value = 1  # cancel flag set
    rds.publish = AsyncMock()
    rds.setex = AsyncMock()
    rds.close = AsyncMock()

    panel_loader = MagicMock(side_effect=AssertionError("must not load"))

    with (
        patch("tinohelm.signal.worker.get_session_factory", return_value=factory),
        patch("tinohelm.signal.worker.aioredis.from_url", return_value=rds),
        patch("tinohelm.signal.worker._load_aligned_panels", panel_loader),
    ):
        from tinohelm.signal.worker import _process_job
        await _process_job(_make_payload(run_id="cancel-pre"), "redis://localhost:6379")

    panel_loader.assert_not_called()
    rds.exists.assert_called_with("tino:signal:cancel:cancel-pre")
    # Status was SET to cancelled.
    cancelled_writes = [p for p in captured if p.get("status") == "cancelled"]
    assert cancelled_writes, "expected one UPDATE setting status='cancelled'"


@pytest.mark.asyncio
async def test_cancel_flag_mid_pipeline_breaks(
    captured_session_factory, fake_eval_result
):
    """Cancel flag set after stage 1 must short-circuit before stage 2."""
    factory, _session, captured, _run = captured_session_factory
    factor, returns = _sample_panels()

    # Cancel returns 0 for the first .exists() (pre-load), then 0 for the
    # ALIGNING check, then 1 for COMPUTING — so the worker must not call
    # the kernel.
    rds = AsyncMock()
    exists_returns = iter([0, 0, 1, 1, 1, 1])

    async def _exists(_key):
        return next(exists_returns)

    rds.exists = _exists
    rds.publish = AsyncMock()
    rds.setex = AsyncMock()
    rds.close = AsyncMock()

    kernel_called = False

    def _fake_kernel(panel, params, constraints):
        nonlocal kernel_called
        kernel_called = True
        return panel  # not used

    with (
        patch("tinohelm.signal.worker.get_session_factory", return_value=factory),
        patch("tinohelm.signal.worker.aioredis.from_url", return_value=rds),
        patch(
            "tinohelm.signal.worker._load_aligned_panels",
            return_value=(factor, returns),
        ),
        patch(
            "tinohelm.signal.worker._resolve_kernel",
            return_value=_fake_kernel,
        ),
    ):
        from tinohelm.signal.worker import _process_job
        await _process_job(_make_payload(run_id="cancel-mid"), "redis://localhost:6379")

    assert not kernel_called, "kernel must not run after cancel between stages"
    cancelled_writes = [p for p in captured if p.get("status") == "cancelled"]
    assert cancelled_writes, "expected status='cancelled' UPDATE"


# ---------------------------------------------------------------------------
# AC-5.1.1: failed job marks status + persists traceback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_job_marks_status_and_error(
    mock_rds, captured_session_factory
):
    """Exception during evaluator → status='failed' + error column populated."""
    factory, _session, captured, _run = captured_session_factory
    factor, returns = _sample_panels()

    with (
        patch("tinohelm.signal.worker.get_session_factory", return_value=factory),
        patch("tinohelm.signal.worker.aioredis.from_url", return_value=mock_rds),
        patch(
            "tinohelm.signal.worker._load_aligned_panels",
            return_value=(factor, returns),
        ),
        patch.object(
            __import__("tinohelm.signal.worker", fromlist=["SignalEvaluator"]).SignalEvaluator,
            "evaluate",
            side_effect=RuntimeError("kernel exploded"),
        ),
    ):
        from tinohelm.signal.worker import _process_job
        await _process_job(_make_payload(), "redis://localhost:6379")

    failed_writes = [
        p
        for p in captured
        if p.get("status") == "failed" and "exploded" in (p.get("error") or "")
    ]
    assert failed_writes, (
        f"expected status='failed' UPDATE with traceback, got: {captured}"
    )

    # signal.failed event published
    publishes = [
        json.loads(c.args[1])
        for c in mock_rds.publish.call_args_list
        if c.args[0] == "tino:signal:events"
    ]
    failed_events = [e for e in publishes if e["type"] == "signal.failed"]
    assert failed_events, "expected signal.failed event"
    assert "exploded" in failed_events[0]["error"]


# ---------------------------------------------------------------------------
# Happy-path completion event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path_publishes_completed_event(
    mock_rds, captured_session_factory, fake_eval_result
):
    """End-to-end happy path: completed event with sharpe/mdd published."""
    factory, _session, captured, _run = captured_session_factory
    factor, returns = _sample_panels()

    with (
        patch("tinohelm.signal.worker.get_session_factory", return_value=factory),
        patch("tinohelm.signal.worker.aioredis.from_url", return_value=mock_rds),
        patch(
            "tinohelm.signal.worker._load_aligned_panels",
            return_value=(factor, returns),
        ),
        patch.object(
            __import__("tinohelm.signal.worker", fromlist=["SignalEvaluator"]).SignalEvaluator,
            "evaluate",
            return_value=fake_eval_result,
        ),
    ):
        from tinohelm.signal.worker import _process_job
        await _process_job(_make_payload(), "redis://localhost:6379")

    publishes = [
        json.loads(c.args[1])
        for c in mock_rds.publish.call_args_list
        if c.args[0] == "tino:signal:events"
    ]
    completed_events = [e for e in publishes if e["type"] == "signal.completed"]
    assert completed_events, "expected one signal.completed event"
    evt = completed_events[0]
    assert evt["sharpe"] == pytest.approx(1.5)
    assert evt["mdd"] == pytest.approx(0.07)

    # Result was persisted with completed status.
    completed_writes = [p for p in captured if p.get("status") == "completed"]
    assert completed_writes, "expected status='completed' UPDATE"


# ---------------------------------------------------------------------------
# Spec rebuild from config
# ---------------------------------------------------------------------------

def test_build_spec_from_config_round_trip():
    """``signal_spec_from_dict`` reproduces all SignalSpec fields."""
    from tinohelm.signal.utils import signal_spec_from_dict as _build_spec_from_config

    cfg = _default_config()
    spec = _build_spec_from_config("my_sig", cfg)
    assert spec.name == "my_sig"
    assert spec.factor_ref == "ret_N@1.0.0"
    assert spec.method == "top_k_long_short"
    assert spec.gross_exposure == 1.0
    assert spec.net_exposure == 0.0
    assert spec.max_position == 0.5
    assert spec.method_params == {"k": 1}
    assert isinstance(spec.cost_model, CostModel)
    assert spec.cost_model.fee_bps_per_side == 4.0


def test_resolve_kernel_unknown_raises():
    from tinohelm.signal.worker import _resolve_kernel

    with pytest.raises(ValueError, match="Unknown signal kernel"):
        _resolve_kernel("not_a_real_method")


def test_resolve_kernel_returns_callable_for_each_method():
    """All 5 declared kernel slugs must resolve to a callable."""
    from tinohelm.signal.worker import _resolve_kernel

    for method in (
        "top_k_long_short",
        "quantile_long_short",
        "threshold_signed",
        "zscore_clip",
        "rank_to_weight",
    ):
        kernel = _resolve_kernel(method)
        assert callable(kernel), f"{method!r} did not resolve to a callable"


# ---------------------------------------------------------------------------
# PR #140 — _load_aligned_panels is wired to the factor DataLayer
# ---------------------------------------------------------------------------

def test_load_aligned_panels_invokes_datalayer_and_kernel():
    """Production wiring: _load_aligned_panels drives Registry + DataLayer + kernel.

    Verifies the panel loader is no longer a stub — it resolves the
    factor kernel from the registry, builds a DataLayer against the PIT
    symbol list persisted in ``config["universe_symbols"]``, and
    computes ``future_returns = close.shift(-1)/close - 1``.

    The factor kernel is mocked to return a deterministic 3-timestamp
    panel so the assertion does not depend on the real ``ret_N`` output.
    """
    from unittest.mock import patch
    import datetime as _dt

    import polars as pl

    from tinohelm.factor.types import FactorSpec, InputSpec
    from tinohelm.signal.types import SignalSpec
    from tinohelm.signal.worker import _load_aligned_panels

    syms = ("BTCUSDT-PERP", "ETHUSDT-PERP")
    ts = pl.Series(
        "ts",
        [_dt.datetime(2024, 1, 1, h) for h in range(3)],
    )
    synthetic_close = pl.DataFrame({
        "ts": ts,
        "BTCUSDT-PERP": [100.0, 110.0, 121.0],
        "ETHUSDT-PERP": [50.0, 55.0, 60.5],
    })

    # Fake factor — InputSpec("close") so the DataLayer only loads close.
    factor_spec = FactorSpec(
        name="fake_momentum",
        category="momentum",
        description="",
        lookback=2,
        input_specs=(InputSpec(field_name="close"),),
    )

    def _fake_kernel(close, params=None):
        # Return the close panel unchanged so we can assert shape.
        # Matches the ret_N signature: ``def ret_N(close: Panel, params=None)``.
        return close

    # DataLayer.load returns the same close panel whether it's asked for
    # the factor input or the future-returns loader.
    def _fake_load(requests, start=None, end=None):
        return {"close": synthetic_close}

    spec = SignalSpec(
        name="test",
        factor_ref="fake_momentum@1.0.0",
        method="top_k_long_short",
        weighting="equal",
        rebalance_freq="1h",
        universe_ref="test_universe",
    )
    config = {
        "universe_symbols": list(syms),
        "start": "2024-01-01",
        "end": "2024-01-02",
    }

    # Patch the registry + DataLayer so the test does not touch disk.
    with (
        patch(
            "tinohelm.factor.registry.Registry.scan",
            return_value=None,
        ),
        patch(
            "tinohelm.factor.registry.Registry.get_kernel",
            return_value=_fake_kernel,
        ),
        patch(
            "tinohelm.factor.registry.Registry.get_spec",
            return_value=factor_spec,
        ),
        patch(
            "tinohelm.factor.data_layer.DataLayer.load",
            side_effect=lambda reqs, start=None, end=None: _fake_load(
                reqs, start=start, end=end
            ),
        ),
    ):
        factor_panel, future_returns = _load_aligned_panels(spec, config)

    # Factor panel is whatever the kernel returned (our synthetic close).
    assert set(factor_panel.columns) == {"ts", *syms}
    assert factor_panel.height == 3

    # Future returns shape matches and the last row is all NaN (shift(-1)).
    assert set(future_returns.columns) == {"ts", *syms}
    assert future_returns.height == 3
    last_row = future_returns.tail(1)
    for s in syms:
        assert last_row[s].item() is None

    # First-row future return = close[1]/close[0] - 1.  For BTC: 110/100-1=0.10.
    first_row = future_returns.head(1)
    assert abs(first_row["BTCUSDT-PERP"].item() - 0.10) < 1e-9
    assert abs(first_row["ETHUSDT-PERP"].item() - 0.10) < 1e-9


def test_load_aligned_panels_rejects_missing_universe_symbols():
    """Legacy run without universe_symbols → ValueError (surfaces as failed)."""
    from tinohelm.signal.types import SignalSpec
    from tinohelm.signal.worker import _load_aligned_panels

    spec = SignalSpec(
        name="test",
        factor_ref="some_factor@1.0.0",
        method="top_k_long_short",
        weighting="equal",
        rebalance_freq="1h",
        universe_ref="x",
    )
    # No universe_symbols key — simulates a legacy pre-resolution row.
    config = {"start": "2024-01-01", "end": "2024-01-02"}

    # Patch registry lookups to succeed so the ValueError comes from the
    # missing-universe branch, not the missing-factor branch.
    from unittest.mock import patch
    from tinohelm.factor.types import FactorSpec

    factor_spec = FactorSpec(name="some_factor", category="x", lookback=1)
    with (
        patch("tinohelm.factor.registry.Registry.scan", return_value=None),
        patch(
            "tinohelm.factor.registry.Registry.get_kernel",
            return_value=lambda **kw: None,
        ),
        patch(
            "tinohelm.factor.registry.Registry.get_spec",
            return_value=factor_spec,
        ),
    ):
        with pytest.raises(ValueError, match="universe_symbols"):
            _load_aligned_panels(spec, config)


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------

def test_start_stop_signal_worker_importable():
    from tinohelm.signal.worker import start_signal_worker, stop_signal_worker

    assert callable(start_signal_worker)
    assert callable(stop_signal_worker)


@pytest.mark.asyncio
async def test_start_stop_signal_worker_lifecycle():
    """``start_signal_worker`` creates a task; ``stop_signal_worker`` cancels it."""
    from tinohelm.signal.worker import (
        _handle,
        start_signal_worker,
        stop_signal_worker,
    )

    _handle.stop()  # ensure clean state from previous tests

    async def _fake_consumer_loop(*args, **kwargs):
        await asyncio.sleep(999)

    with patch(
        "tinohelm.signal.worker.consumer_loop",
        new=_fake_consumer_loop,
    ):
        task = start_signal_worker("redis://localhost:6379")

    assert _handle.is_running()
    stop_signal_worker()
    assert not _handle.is_running()
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Recovery — running → queued + full JSON payloads onto Redis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recover_interrupted_jobs_flips_running_to_queued():
    """recover_interrupted_jobs flips running rows to queued + re-enqueues payloads."""
    from tinohelm.signal.worker import recover_interrupted_jobs

    rds = AsyncMock()
    rds.delete = AsyncMock()
    rds.rpush = AsyncMock()

    captured_updates: list[dict] = []
    select_calls: list = []

    async def _execute(stmt):
        try:
            compiled = stmt.compile()
            params = dict(compiled.params)
        except Exception:
            params = {}
        if params.get("status") == "queued" and "progress" in params:
            # The running → queued UPDATE.
            captured_updates.append(params)
            return MagicMock(rowcount=2)
        # The SELECT for queued ids.
        select_calls.append(stmt)
        row_result = MagicMock()
        row_result.all.return_value = [
            ("r1", "sig_a", {"periods_per_year": 365}),
            ("r2", "sig_b", {"periods_per_year": 252, "trace_id": "t2"}),
        ]
        return row_result

    session = AsyncMock()
    session.execute = _execute
    session.commit = AsyncMock()

    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with patch("tinohelm.signal.worker.get_session_factory", return_value=factory):
        recovered = await recover_interrupted_jobs(rds)

    assert recovered == 2
    assert captured_updates, "expected one UPDATE flipping running → queued"
    rds.delete.assert_called_with("tino:signal:queue")
    assert rds.rpush.await_count == 2
    payloads = [json.loads(call.args[1]) for call in rds.rpush.await_args_list]
    assert payloads == [
        {
            "run_id": "r1",
            "signal_name": "sig_a",
            "config": {"periods_per_year": 365},
        },
        {
            "run_id": "r2",
            "signal_name": "sig_b",
            "config": {"periods_per_year": 252, "trace_id": "t2"},
        },
    ]


# ---------------------------------------------------------------------------
# Source routing — market_cap must NOT fall through to "bar"
# ---------------------------------------------------------------------------

def test_load_aligned_panels_routes_market_cap_to_market_cap_source():
    """market_cap field must be routed to source='market_cap', never 'bar'.

    Regression guard for the bug where _load_aligned_panels hard-coded
    ``source = "funding_rate" if field == "funding_rate" else "bar"``,
    which incorrectly routed market_cap → bar → DataLayer read failure.
    """
    from unittest.mock import patch, call
    import datetime as _dt

    import polars as pl

    from tinohelm.factor.types import FactorSpec, InputSpec
    from tinohelm.signal.types import SignalSpec
    from tinohelm.signal.worker import _load_aligned_panels

    syms = ("BTCUSDT-PERP",)
    ts = pl.Series("ts", [_dt.datetime(2024, 1, 1, h) for h in range(3)])
    synthetic_panel = pl.DataFrame({
        "ts": ts,
        "BTCUSDT-PERP": [1.0e10, 1.1e10, 1.2e10],
    })

    factor_spec = FactorSpec(
        name="fake_logmcap",
        category="size",
        description="",
        lookback=1,
        input_specs=(InputSpec(field_name="market_cap"),),
    )

    captured_requests: list = []

    def _fake_load(requests, start=None, end=None):
        captured_requests.extend(requests)
        # Return only the fields that were requested so the kernel
        # never sees unexpected keyword arguments (close is loaded
        # separately via close_requests).
        fields = {r.field_name for r in requests}
        return {f: synthetic_panel for f in fields}

    spec = SignalSpec(
        name="test_mcap",
        factor_ref="fake_logmcap@1.0.0",
        method="top_k_long_short",
        weighting="equal",
        rebalance_freq="1h",
        universe_ref="test_universe",
    )
    config = {
        "universe_symbols": list(syms),
        "start": "2024-01-01",
        "end": "2024-01-02",
    }

    def _fake_kernel(market_cap, params=None):
        return market_cap

    with (
        patch("tinohelm.factor.registry.Registry.scan", return_value=None),
        patch("tinohelm.factor.registry.Registry.get_kernel", return_value=_fake_kernel),
        patch("tinohelm.factor.registry.Registry.get_spec", return_value=factor_spec),
        patch(
            "tinohelm.factor.data_layer.DataLayer.load",
            side_effect=lambda reqs, start=None, end=None: _fake_load(reqs, start=start, end=end),
        ),
    ):
        _load_aligned_panels(spec, config)

    # Every DataRequest for market_cap must use source="market_cap".
    mcap_requests = [r for r in captured_requests if r.field_name == "market_cap"]
    assert mcap_requests, "expected at least one DataRequest for market_cap"
    for req in mcap_requests:
        assert req.source == "market_cap", (
            f"market_cap field routed to source={req.source!r}, expected 'market_cap'"
        )
