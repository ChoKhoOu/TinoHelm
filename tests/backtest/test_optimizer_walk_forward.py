"""Tests for BacktestOptimizer._objective_walk_forward subprocess path.

All tests use mock subprocess.run — no real NT engine or runner_cli invoked.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

optimizer_mod = pytest.importorskip("tinohelm.backtest.optimizer")

FAIL_VALUE = optimizer_mod.FAIL_VALUE
BacktestOptimizer = optimizer_mod.BacktestOptimizer


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_optimizer() -> BacktestOptimizer:
    """Create a BacktestOptimizer with minimal required fields stubbed out."""
    opt = object.__new__(BacktestOptimizer)
    # Required instance attrs referenced by _run_backtest_subprocess and
    # _objective_walk_forward
    opt.strategy_path = "/fake/strategy.py"
    opt.config_path = None
    opt.catalog_path = "/fake/catalog"
    opt.symbol = "BTCUSDT-PERP"
    opt.interval = "1m"
    opt.fitness_objective = "sharpe"
    opt.param_ranges = {}
    opt.strategy_params = {}
    opt.pruning = False
    # Shared engine state not needed for walk-forward
    opt._shared_runner = None
    opt._shared_engine = None
    opt._shared_strategy_bundle = None
    opt._shared_starting_balance = 0.0
    return opt


def _fake_trial(number: int = 0) -> MagicMock:
    """Return a duck-typed Optuna trial stub."""
    trial = MagicMock()
    trial.number = number
    trial.params = {}
    trial.should_prune.return_value = False
    # _suggest_params iterates param_ranges; with empty ranges it returns
    # strategy_params copy — no trial.suggest_* calls needed
    return trial


def _make_windows(n: int = 3) -> list[tuple[date, date, date, date]]:
    """Build n non-overlapping fold windows covering 2024."""
    from datetime import date, timedelta
    windows = []
    base = date(2024, 1, 1)
    fold_days = 30
    for i in range(n):
        tr_s = base + timedelta(days=i * fold_days * 2)
        tr_e = tr_s + timedelta(days=fold_days - 1)
        te_s = tr_e + timedelta(days=1)
        te_e = te_s + timedelta(days=fold_days - 1)
        windows.append((tr_s, tr_e, te_s, te_e))
    return windows


def _ok_stdout(fitness: float = 0.5) -> str:
    return json.dumps({"status": "ok", "fitness": fitness, "metrics": {}}) + "\n"


def _completed_proc(fitness: float = 0.5) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=_ok_stdout(fitness),
        stderr="",
    )


def _failed_proc() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="oops",
    )


# ---------------------------------------------------------------------------
# Test 1: subprocess.run called once per fold, fitness aggregated correctly
# ---------------------------------------------------------------------------

def test_walk_forward_uses_subprocess(monkeypatch):
    """_objective_walk_forward calls subprocess.run exactly N=folds times.

    Each call returns fitness=0.5; the final objective value must be 0.5
    (mean of [0.5, 0.5, 0.5]).
    """
    call_count = 0

    def fake_run(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _completed_proc(fitness=0.5)

    monkeypatch.setattr("subprocess.run", fake_run)

    opt = _make_optimizer()
    trial = _fake_trial(number=0)
    windows = _make_windows(n=3)

    result = opt._objective_walk_forward(trial, windows)

    assert call_count == 3, f"Expected 3 subprocess.run calls, got {call_count}"
    assert result == pytest.approx(0.5), f"Expected mean fitness 0.5, got {result}"


# ---------------------------------------------------------------------------
# Test 2: subprocess failure on one fold → FAIL_VALUE for that fold,
#         other folds contribute normally to the mean
# ---------------------------------------------------------------------------

def test_walk_forward_handles_failure(monkeypatch):
    """When fold 0 subprocess exits non-zero, that fold appends FAIL_VALUE.

    Folds 1 and 2 succeed (fitness=0.7, 0.9); the final result must equal
    mean([0.7, 0.9]) = 0.8, ignoring the FAIL_VALUE fold.
    """
    responses = [_failed_proc(), _completed_proc(0.7), _completed_proc(0.9)]
    call_idx = 0

    def fake_run(*args, **kwargs):
        nonlocal call_idx
        resp = responses[call_idx]
        call_idx += 1
        return resp

    monkeypatch.setattr("subprocess.run", fake_run)

    opt = _make_optimizer()
    trial = _fake_trial()
    windows = _make_windows(n=3)

    result = opt._objective_walk_forward(trial, windows)

    assert call_idx == 3, "All 3 folds must be attempted"
    assert result == pytest.approx((0.7 + 0.9) / 2), (
        f"Expected mean of successful folds (0.8), got {result}"
    )


# ---------------------------------------------------------------------------
# Test 3: subprocess.TimeoutExpired on one fold → FAIL_VALUE for that fold
# ---------------------------------------------------------------------------

def test_walk_forward_handles_timeout(monkeypatch):
    """When fold 0 times out, that fold appends FAIL_VALUE.

    Folds 1 and 2 succeed; result must be mean of their fitness values.
    """
    responses_iter = iter([
        subprocess.TimeoutExpired(cmd=[], timeout=60),
        _completed_proc(0.6),
        _completed_proc(0.4),
    ])

    def fake_run(*args, **kwargs):
        resp = next(responses_iter)
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr("subprocess.run", fake_run)

    opt = _make_optimizer()
    trial = _fake_trial()
    windows = _make_windows(n=3)

    result = opt._objective_walk_forward(trial, windows)

    assert result == pytest.approx((0.6 + 0.4) / 2), (
        f"Expected mean of successful folds (0.5), got {result}"
    )
