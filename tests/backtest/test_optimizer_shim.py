"""Tests for the thin Optuna-coupled shims in ``optimizer.py``.

These tests cover the wire-up between the Optuna callback API and the
NT/optuna-free :class:`~tinohelm.backtest.optimizer_helpers.PatienceTracker`
state machine — the place where extraction-style refactors are most prone
to silent regressions.

The full ``BacktestOptimizer.run()`` orchestration (Redis + DB + optuna
study) is not exercised here; that path is integration-tested via the
e2e backtest scripts.  What this file pins is:

1. ``_PatienceCallback`` calls ``study.stop()`` exactly when its internal
   :class:`PatienceTracker` says we should stop, and never sooner.
2. ``_PatienceCallback`` correctly forwards ``trial.value`` (including
   ``None`` for pruned trials).
3. The backwards-compatible private aliases (``_FAIL_VALUE``,
   ``_split_dates``, etc.) still resolve to the canonical helpers — the
   API route :mod:`tinohelm.api.routes.optimize` and any external code
   that imported them must not break.

We construct the callback without importing optuna; the callback only
*types* its parameters as Optuna types, the runtime behavior is duck-typed.
"""
from __future__ import annotations

import pytest

# Importing optimizer.py requires optuna to either be installed or for
# the module's `try: import optuna` to succeed against None.  We can
# always import the module (it has the try/except guard), but the
# callback itself uses optuna types in annotations only — runtime code is
# safe to call without optuna present.
optimizer = pytest.importorskip("tinohelm.backtest.optimizer")
helpers = pytest.importorskip("tinohelm.backtest.optimizer_helpers")


class _FakeStudy:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeTrial:
    def __init__(self, value):
        self.value = value


class TestPatienceCallback:
    """The optuna-facing shim must delegate purely to PatienceTracker."""

    def test_does_not_stop_on_first_trial(self):
        cb = optimizer._PatienceCallback(patience=2)
        study = _FakeStudy()
        cb(study, _FakeTrial(1.0))
        assert study.stopped is False

    def test_stops_after_patience_no_improve(self):
        cb = optimizer._PatienceCallback(patience=2)
        study = _FakeStudy()
        cb(study, _FakeTrial(1.0))   # first — sets best
        cb(study, _FakeTrial(0.5))   # 1 no-improve
        assert study.stopped is False
        cb(study, _FakeTrial(0.5))   # 2 no-improve — STOP
        assert study.stopped is True

    def test_improvement_resets(self):
        cb = optimizer._PatienceCallback(patience=2)
        study = _FakeStudy()
        cb(study, _FakeTrial(1.0))
        cb(study, _FakeTrial(0.5))   # 1 no-improve
        cb(study, _FakeTrial(2.0))   # improve — reset
        cb(study, _FakeTrial(0.5))   # 1 no-improve again
        assert study.stopped is False

    def test_pruned_trial_value_none_counts_as_no_improve(self):
        cb = optimizer._PatienceCallback(patience=2)
        study = _FakeStudy()
        cb(study, _FakeTrial(1.0))
        cb(study, _FakeTrial(None))  # pruned counts as no-improve
        cb(study, _FakeTrial(None))  # 2 no-improve — STOP
        assert study.stopped is True

    def test_stop_is_idempotent(self):
        # Once stopped, we can keep getting called without crashing.
        cb = optimizer._PatienceCallback(patience=1)
        study = _FakeStudy()
        cb(study, _FakeTrial(1.0))
        cb(study, _FakeTrial(0.5))
        assert study.stopped is True
        cb(study, _FakeTrial(0.5))  # extra calls are harmless
        cb(study, _FakeTrial(0.5))
        assert study.stopped is True


class TestBackwardCompatAliases:
    """The underscore-prefixed private names MUST keep resolving.

    External callers (api/routes/optimize.py, downstream tooling) imported
    these names directly.  Removing them silently would break production
    without a single failing test, which is exactly the failure mode this
    test exists to prevent.
    """

    @pytest.mark.parametrize("private,public", [
        ("_FAIL_VALUE", "FAIL_VALUE"),
        ("_split_dates", "split_dates"),
        ("_walk_forward_windows", "walk_forward_windows"),
        ("_extract_fitness", "extract_fitness"),
        ("_auto_n_trials", "auto_n_trials"),
        ("_auto_sampler", "auto_sampler"),
        ("_auto_workers", "auto_workers"),
        ("_slim_result", "slim_result"),
        ("_compute_dsr", "compute_dsr"),
        ("_compute_param_sensitivity", "compute_param_sensitivity"),
        ("_compute_param_stability", "compute_param_stability"),
    ])
    def test_alias_resolves_to_canonical(self, private, public):
        # Each underscore alias must resolve to the same object exported
        # from optimizer_helpers.  ``is`` (identity) is intentional —
        # equality-only would let an alias drift to a re-implementation.
        assert getattr(optimizer, private) is getattr(helpers, public), (
            f"Backwards-compat alias optimizer.{private} no longer points "
            f"to optimizer_helpers.{public} — external imports may break."
        )


class TestSubprocessPayload:
    def test_fold_subprocess_payload_omits_interval_when_empty(self, monkeypatch, tmp_path):
        opt = optimizer.BacktestOptimizer(
            strategy_path="fake/strat.py:FakeStrategy",
            config_path="fake/strat.py:FakeStrategyConfig",
            symbol="BTCUSDT-PERP",
            interval="",
            start_date=__import__("datetime").date(2026, 1, 1),
            end_date=__import__("datetime").date(2026, 2, 1),
            catalog_path="/tmp/catalog",
            n_trials=1,
            fitness_objective="sharpe",
            train_pct=0.8,
            db_url="sqlite:///:memory:",
            redis_url="redis://localhost:6379",
            optimization_id=1,
        )

        captured = {}

        class _TmpFile:
            name = str(tmp_path / "fold.json")
            def write(self, s):
                captured.setdefault("writes", []).append(s)
            def flush(self):
                return None
            def close(self):
                return None

        monkeypatch.setattr(optimizer.tempfile, "NamedTemporaryFile", lambda **kwargs: _TmpFile())
        monkeypatch.setattr(optimizer.json, "dump", lambda payload, fh: captured.setdefault("payload", payload))
        monkeypatch.setattr(optimizer.subprocess, "run", lambda *args, **kwargs: type("P", (), {"returncode": 0, "stdout": '{"status":"ok","fitness":1.0,"metrics":{}}', "stderr": ""})())
        monkeypatch.setattr(optimizer.os, "unlink", lambda path: None)

        result = opt._run_backtest_subprocess({}, __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 10))

        assert result["_fitness"] == 1.0
        assert "interval" not in captured["payload"]


class TestPublicRexports:
    """The new public re-exports that ``run()`` consumes must be accessible
    via ``from tinohelm.backtest.optimizer import X`` so they're a single
    self-contained surface for any future caller."""

    @pytest.mark.parametrize("name", [
        "FAIL_VALUE",
        "FITNESS_METRICS",
        "PROGRESS_STATUS_RUNNING",
        "PROGRESS_STATUS_COMPLETED",
        "PatienceTracker",
        "auto_n_trials",
        "auto_patience",
        "auto_sampler",
        "auto_workers",
        "build_full_result",
        "build_progress_payload",
        "build_walk_forward_fold_record",
        "compute_dsr",
        "compute_param_sensitivity",
        "compute_param_stability",
        "extract_fitness",
        "select_best_params",
        "serialize_trial",
        "slim_result",
        "split_dates",
        "walk_forward_windows",
    ])
    def test_symbol_importable_from_optimizer(self, name):
        assert hasattr(optimizer, name), (
            f"optimizer.{name} not importable — Phase-2 extraction broke "
            f"the consolidated import surface."
        )
