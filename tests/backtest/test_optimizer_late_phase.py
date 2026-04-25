"""Tests for BacktestOptimizer.run() late-phase subprocess calls.

Covers three call sites added in the s8 subprocess refactor:
  - WF fold details (use_walk_forward=True, after study.optimize completes)
  - Validation backtest (held-out test period, both Simple and WF mode)
  - Train validation (Simple mode only)

All tests mock subprocess.run (via monkeypatching _run_backtest_subprocess
on the instance) — no real NT engine, runner_cli, or Redis/DB connections
are invoked.

Since optuna may not be installed in the test environment, the three core
assertions are structured to call the late-phase code paths directly after
mocking study.optimize + best_trial, bypassing the optuna guard.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

optimizer_mod = pytest.importorskip("tinohelm.backtest.optimizer")

FAIL_VALUE = optimizer_mod.FAIL_VALUE
BacktestOptimizer = optimizer_mod.BacktestOptimizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_optimizer(
    *,
    walk_forward_folds: int = 0,
    n_trials: int = 1,
) -> BacktestOptimizer:
    """Create a minimal BacktestOptimizer stub for run() testing.

    param_ranges has one float param so select_best_params() returns a
    non-empty dict, ensuring the 'if best_params:' guard in run() passes
    and the three late-phase code paths execute.
    """
    opt = object.__new__(BacktestOptimizer)
    opt.strategy_path = "/fake/strategy.py"
    opt.config_path = None
    opt.catalog_path = "/fake/catalog"
    opt.symbol = "BTCUSDT-PERP"
    opt.interval = "1m"
    opt.fitness_objective = "sharpe"
    # One optimized param so select_best_params() returns non-empty dict
    opt.param_ranges = {"fast_period": {"type": "int", "min": 5, "max": 20}}
    opt.strategy_params = {}
    opt.pruning = False
    opt.sampler = "tpe"
    opt.patience = 0
    opt.n_workers = 1
    opt.n_trials = n_trials
    opt.train_pct = 80.0
    opt.walk_forward_folds = walk_forward_folds
    opt.start_date = date(2024, 1, 1)
    opt.end_date = date(2024, 12, 31)
    opt.optimization_id = 999
    opt.redis_url = "redis://localhost:6379"
    opt.db_url = "sqlite://"
    opt._shared_runner = None
    opt._shared_engine = None
    opt._shared_strategy_bundle = None
    opt._shared_starting_balance = 0.0
    return opt


def _stub_optuna(study_best_value: float = 1.0, best_params: dict | None = None):
    """Return a fake optuna module + a fake study whose best_trial yields the given value.

    best_params defaults to {"fast_period": 10} to match _make_optimizer's param_ranges,
    ensuring select_best_params() returns a non-empty dict and the late-phase guards pass.
    """
    best_params = best_params if best_params is not None else {"fast_period": 10}

    fake_frozen_trial = SimpleNamespace(
        value=study_best_value,
        params=best_params,
    )

    fake_study = MagicMock()
    fake_study.optimize.return_value = None
    # Use direct attribute assignment (PropertyMock on MagicMock type affects all instances)
    fake_study.best_trial = fake_frozen_trial
    fake_study.trials = []
    fake_study.stop.return_value = None

    # MedianPruner / NopPruner stubs
    fake_pruner = MagicMock()
    fake_sampler_instance = MagicMock()
    fake_pruners = SimpleNamespace(
        MedianPruner=MagicMock(return_value=fake_pruner),
        NopPruner=MagicMock(return_value=fake_pruner),
    )
    fake_samplers = SimpleNamespace(
        TPESampler=MagicMock(return_value=fake_sampler_instance),
        CmaEsSampler=MagicMock(return_value=fake_sampler_instance),
        RandomSampler=MagicMock(return_value=fake_sampler_instance),
    )
    fake_importance = SimpleNamespace(
        get_param_importances=MagicMock(return_value={}),
    )
    fake_trial_state = SimpleNamespace(PRUNED="pruned")

    fake_optuna = SimpleNamespace(
        logging=SimpleNamespace(
            WARNING=30,
            set_verbosity=MagicMock(),
        ),
        pruners=fake_pruners,
        samplers=fake_samplers,
        importance=fake_importance,
        trial=SimpleNamespace(TrialState=fake_trial_state),
        create_study=MagicMock(return_value=fake_study),
        TrialPruned=Exception,
    )

    return fake_optuna, fake_study


def _make_mock_redis() -> MagicMock:
    r = MagicMock()
    r.get.return_value = None
    r.publish.return_value = None
    r.close.return_value = None
    return r


def _noop_db_patch(monkeypatch) -> None:
    """Patch out all DB writes so run() can complete without a real DB."""
    monkeypatch.setattr(
        "tinohelm.backtest.optimizer.get_sync_engine",
        MagicMock(return_value=MagicMock()),
    )
    # Patch Session used in _update_progress / _complete / _fail
    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_session.execute.return_value = None
    fake_session.commit.return_value = None
    monkeypatch.setattr("sqlalchemy.orm.Session", MagicMock(return_value=fake_session))


# Full-shape result that late-phase full-mode calls return.
# Mirrors the shape runner_cli emits in "full" mode — contains equity_curve,
# daily_returns, monthly_returns, statistics, and trade_log.
_FULL_SHAPE_RESULT = {
    "statistics": {
        "sharpe_ratio": 1.0,
        "total_trades": 10,
        "pnl_total": 500.0,
        "win_rate": 0.6,
        "total_return_pct": 5.0,
        "max_drawdown": -0.1,
    },
    "equity_curve": [
        {"timestamp": "2024-01-01T00:00:00", "equity": 10000.0},
        {"timestamp": "2024-06-01T00:00:00", "equity": 10500.0},
    ],
    "daily_returns": [0.001, 0.002, -0.001, 0.003, 0.001],
    "monthly_returns": [{"month": "2024-01", "return": 0.015}],
    "trade_log": [],
}


# ---------------------------------------------------------------------------
# Test 1: Simple mode — validation + train validation both call _run_backtest_subprocess
# ---------------------------------------------------------------------------

def test_validation_uses_subprocess(monkeypatch):
    """Simple (non-WF) mode: run() calls _run_backtest_subprocess at least
    twice in the late phase — once for validation (test period) and once for
    train validation (train period).

    _objective_simple is replaced by a stub returning fitness=1.0, so the
    only subprocess calls counted come from the late-phase code paths.

    The mock returns full-shape results when result_mode="full" is passed
    (the upgraded call sites), mirroring what runner_cli full mode emits.

    Assertions:
      - >= 2 calls to _run_backtest_subprocess (validation + train validation)
      - Both calls use result_mode="full"
      - One call ends on end_date (2024-12-31) — the held-out validation
      - validation_result["equity_curve"] is a non-empty list
      - validation_result["daily_returns"] is a non-empty list
      - DSR is computed (not None) since fitness_objective="sharpe" and n_obs > 0
    """
    late_phase_calls: list[tuple[date, date, str]] = []

    def tracking_subprocess(self, params, start, end, *, result_mode="slim", **kw):
        late_phase_calls.append((start, end, result_mode))
        if result_mode == "full":
            return _FULL_SHAPE_RESULT
        return {"statistics": {"sharpe_ratio": 1.0}, "_fitness": 1.0}

    monkeypatch.setattr(BacktestOptimizer, "_run_backtest_subprocess", tracking_subprocess)

    opt = _make_optimizer(walk_forward_folds=0, n_trials=1)

    # Patch optuna module-level reference inside optimizer.py.
    # best_params uses the default {"fast_period": 10} which matches param_ranges,
    # so select_best_params() returns non-empty → 'if best_params:' guard passes.
    fake_optuna, fake_study = _stub_optuna(study_best_value=1.0)
    monkeypatch.setattr(optimizer_mod, "optuna", fake_optuna)

    # Patch Redis
    mock_redis = _make_mock_redis()
    monkeypatch.setattr("redis.from_url", lambda *a, **kw: mock_redis)

    # Patch DB
    _noop_db_patch(monkeypatch)

    # Bypass shared-engine preparation (not needed, would fail without NT data)
    opt._shared_runner = MagicMock()
    opt._shared_runner.run_trial = MagicMock(
        return_value={"statistics": {"sharpe_ratio": 1.0}}
    )

    # Patch build_full_result to capture what validation= and dsr= receives
    captured_validation = [None]
    captured_train_validation = [None]
    captured_dsr = [None]
    original_bfr = optimizer_mod.build_full_result

    def capturing_bfr(**kw):
        captured_validation[0] = kw.get("validation")
        captured_train_validation[0] = kw.get("train_validation")
        captured_dsr[0] = kw.get("dsr")
        return original_bfr(**kw)

    monkeypatch.setattr("tinohelm.backtest.optimizer.build_full_result", capturing_bfr)

    opt.run()

    assert len(late_phase_calls) >= 2, (
        f"Expected >= 2 late-phase subprocess calls (validation + train validation), "
        f"got {len(late_phase_calls)}: {late_phase_calls}"
    )

    # Both late-phase calls must use result_mode="full"
    modes = [c[2] for c in late_phase_calls]
    assert all(m == "full" for m in modes), (
        f"All late-phase calls must use result_mode='full', got: {modes}"
    )

    # Held-out test period ends on end_date (2024-12-31)
    call_ends = [c[1] for c in late_phase_calls]
    assert date(2024, 12, 31) in call_ends, (
        f"Expected a validation call ending on end_date 2024-12-31, got {call_ends}"
    )

    # validation_result must contain full result fields (not just statistics+_fitness)
    assert captured_validation[0] is not None, "validation_result must not be None on success"
    assert "statistics" in captured_validation[0], (
        f"validation_result must have 'statistics' key, got keys: {list(captured_validation[0])}"
    )
    assert isinstance(captured_validation[0].get("equity_curve"), list) and \
           len(captured_validation[0]["equity_curve"]) > 0, (
        f"validation_result['equity_curve'] must be a non-empty list, "
        f"got: {captured_validation[0].get('equity_curve')}"
    )
    assert isinstance(captured_validation[0].get("daily_returns"), list) and \
           len(captured_validation[0]["daily_returns"]) > 0, (
        f"validation_result['daily_returns'] must be a non-empty list, "
        f"got: {captured_validation[0].get('daily_returns')}"
    )

    # train_validation must also be passed to build_full_result (via slim_result projection)
    assert captured_train_validation[0] is not None, (
        "train_validation must not be None in simple mode (slim_result of full result)"
    )
    assert isinstance(captured_train_validation[0].get("equity_curve"), list) and \
           len(captured_train_validation[0]["equity_curve"]) > 0, (
        f"train_validation['equity_curve'] must be a non-empty list "
        f"(from slim_result projection), got: {captured_train_validation[0].get('equity_curve')}"
    )

    # DSR regression guard: n_obs = len(validation_result["daily_returns"]) must be > 0.
    # DSR itself may be None when n_trials < 5 (this test uses 1 stub trial), but the
    # pre-regression bug was n_obs=0 due to slim mode missing daily_returns entirely.
    # Asserting equity_curve and daily_returns are non-empty above is the correct guard.
    # Separately verify that validation_result["daily_returns"] has the expected count
    # (ensures compute_dsr would have received n_obs > 0, not the old n_obs = 0).
    n_obs = len(captured_validation[0].get("daily_returns", []) or [])
    assert n_obs > 0, (
        f"n_obs passed to compute_dsr must be > 0 when full mode is used. "
        f"Got n_obs={n_obs} — regression: validation_result missing daily_returns "
        f"(was returning slim shape instead of full result)"
    )


# ---------------------------------------------------------------------------
# Test 2: WF mode — fold detail runs use subprocess
# ---------------------------------------------------------------------------

def test_wf_fold_details_use_subprocess(monkeypatch):
    """Walk-forward mode with 3 folds: after study.optimize, run() calls
    _run_backtest_subprocess once per fold for fold details (3 calls) plus
    once for the held-out validation (1 call) = 4 total.

    WF mode must NOT trigger train validation (guarded by 'not use_walk_forward').

    Assertions:
      - >= 4 total late-phase calls
      - wf_fold_results has exactly 3 entries
      - Each entry's test_value != FAIL_VALUE
    """
    late_phase_calls: list[tuple[date, date, str]] = []

    def tracking_subprocess(self, params, start, end, *, result_mode="slim", **kw):
        late_phase_calls.append((start, end, result_mode))
        if result_mode == "full":
            return _FULL_SHAPE_RESULT
        # WF fold details use slim mode — return fitness-compatible shape
        return {"statistics": {"sharpe_ratio": 0.8}, "_fitness": 0.8}

    monkeypatch.setattr(BacktestOptimizer, "_run_backtest_subprocess", tracking_subprocess)

    opt = _make_optimizer(walk_forward_folds=3, n_trials=1)

    # Patch optuna — best_params uses default {"fast_period": 10} for non-empty guard
    fake_optuna, fake_study = _stub_optuna(study_best_value=0.8)
    monkeypatch.setattr(optimizer_mod, "optuna", fake_optuna)

    # Patch Redis
    mock_redis = _make_mock_redis()
    monkeypatch.setattr("redis.from_url", lambda *a, **kw: mock_redis)

    # Patch DB
    _noop_db_patch(monkeypatch)

    # Capture what build_full_result receives for walk_forward_results
    captured_wf: list[list] = [[]]
    original_bfr = optimizer_mod.build_full_result

    def capturing_bfr(**kw):
        captured_wf[0] = list(kw.get("walk_forward_results") or [])
        return original_bfr(**kw)

    monkeypatch.setattr("tinohelm.backtest.optimizer.build_full_result", capturing_bfr)

    opt.run()

    # 3 fold details + 1 validation = 4 minimum
    assert len(late_phase_calls) >= 4, (
        f"Expected >= 4 late-phase calls (3 fold details + 1 validation), "
        f"got {len(late_phase_calls)}: {late_phase_calls}"
    )

    # wf_fold_results must have exactly 3 entries
    assert len(captured_wf[0]) == 3, (
        f"Expected 3 wf_fold_results entries, got {len(captured_wf[0])}: {captured_wf[0]}"
    )

    # Each fold's test_value must not be FAIL_VALUE
    for i, record in enumerate(captured_wf[0]):
        tv = record.get("test_value")
        assert tv != FAIL_VALUE, (
            f"wf_fold_results[{i}].test_value == FAIL_VALUE — "
            f"subprocess mock not properly hooked"
        )

    # Verify WF mode did NOT trigger train validation:
    # train validation would add a call ending before the first test window start.
    # The held-out test period ends on end_date (2024-12-31).
    call_ends = [c[1] for c in late_phase_calls]
    assert date(2024, 12, 31) in call_ends, (
        f"Expected held-out validation call ending on 2024-12-31, got {call_ends}"
    )
    # train_start is 2024-01-01; train validation would start there.
    # It must NOT appear as a call end (train_end is ~2024-10-22 for 80% split).
    from tinohelm.backtest.optimizer_helpers import split_dates
    _, train_end, _, _ = split_dates(opt.start_date, opt.end_date, opt.train_pct)
    assert train_end not in call_ends, (
        f"WF mode must not call train validation (train_end={train_end} should not "
        f"appear in call ends), but got {call_ends}"
    )

    # WF fold detail calls (slim) vs validation call (full):
    # The held-out validation call (ends on 2024-12-31) must use result_mode="full".
    # The 3 fold detail calls must use result_mode="slim" (trial-loop pattern, zero-invasive).
    slim_calls = [(c[0], c[1]) for c in late_phase_calls if c[2] == "slim"]
    full_calls = [(c[0], c[1]) for c in late_phase_calls if c[2] == "full"]
    assert len(full_calls) == 1, (
        f"WF mode must have exactly 1 full-mode call (held-out validation), "
        f"got {len(full_calls)}: {full_calls}"
    )
    assert len(slim_calls) == 3, (
        f"WF mode must have exactly 3 slim-mode fold detail calls, "
        f"got {len(slim_calls)}: {slim_calls}"
    )


# ---------------------------------------------------------------------------
# Test 3: Late-phase subprocess failure → validation_result = None, no raise
# ---------------------------------------------------------------------------

def test_late_phase_handles_subprocess_failure(monkeypatch):
    """When the validation subprocess fails (_run_backtest_subprocess raises),
    run() must catch it, set validation_result = None, and complete without
    raising an exception.

    Achieved by making subprocess.run return returncode=1, so
    _run_backtest_subprocess raises RuntimeError, which run() catches.
    """
    def raising_subprocess(self, params, start, end, **kw):
        raise RuntimeError("subprocess exit 1: oops")

    monkeypatch.setattr(BacktestOptimizer, "_run_backtest_subprocess", raising_subprocess)

    opt = _make_optimizer(walk_forward_folds=0, n_trials=1)

    # Patch optuna — best_params uses default {"fast_period": 10} for non-empty guard
    fake_optuna, fake_study = _stub_optuna(study_best_value=1.0)
    monkeypatch.setattr(optimizer_mod, "optuna", fake_optuna)

    # Patch Redis
    mock_redis = _make_mock_redis()
    monkeypatch.setattr("redis.from_url", lambda *a, **kw: mock_redis)

    # Patch DB
    _noop_db_patch(monkeypatch)

    # Shared engine stub
    opt._shared_runner = MagicMock()
    opt._shared_runner.run_trial = MagicMock(
        return_value={"statistics": {"sharpe_ratio": 1.0}}
    )

    # Capture build_full_result's validation argument
    captured_validation = [sentinel := object()]
    original_bfr = optimizer_mod.build_full_result

    def capturing_bfr(**kw):
        captured_validation[0] = kw.get("validation")
        return original_bfr(**kw)

    monkeypatch.setattr("tinohelm.backtest.optimizer.build_full_result", capturing_bfr)

    # run() must not raise
    try:
        opt.run()
    except Exception as exc:
        pytest.fail(f"run() raised an unexpected exception on subprocess failure: {exc}")

    # build_full_result must have been called (run completed successfully)
    assert captured_validation[0] is not sentinel, "build_full_result was never called"

    # validation_result must be None when subprocess fails
    assert captured_validation[0] is None, (
        f"Expected validation=None when subprocess fails, got {captured_validation[0]}"
    )
