"""Integration tests for ``POST /api/factor/params_grid``.

Uses FastAPI ``TestClient`` with mocked DataLayer and params_grid —
no real Parquet I/O, but the endpoint logic (DataRequest construction +
data_layer.load call shape) is tested end-to-end.

Coverage
--------
- 404 when factor_name is not in registry
- 422 when universe is empty
- DataLayer.load is called with a list[DataRequest] (not str / EvalConfig)
- params_grid is called and its result is forwarded as ``candidates``
- Response shape: ``{factor_name, candidates}``
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tinohelm.api.routes import factor as factor_routes


# ---------------------------------------------------------------------------
# Test app
# ---------------------------------------------------------------------------

test_app = FastAPI()
test_app.include_router(factor_routes.router)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path):
    """Client with mocked DB and Redis dependencies."""
    from tinohelm.api.deps import get_db, get_redis

    async def _db():
        session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)
        session.add = MagicMock()
        session.commit = AsyncMock()
        yield session

    async def _rds():
        return AsyncMock()

    test_app.dependency_overrides[get_db] = _db
    test_app.dependency_overrides[get_redis] = _rds
    with TestClient(test_app) as c:
        yield c
    test_app.dependency_overrides.clear()


@pytest.fixture()
def mock_factor_spec():
    """A minimal FactorSpec mock with input_specs that returns a 'close' DataRequest."""
    spec = MagicMock()
    spec.params = {"n": 5}
    spec.lookback = 5

    # input_specs: one field 'close' with no forced frequency
    inp = MagicMock()
    inp.field_name = "close"
    inp.frequency = None
    spec.input_specs = [inp]
    return spec


@pytest.fixture()
def mock_close_panel():
    """A minimal polars DataFrame representing the close price panel."""
    import polars as pl
    from datetime import datetime, timedelta

    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(10)]
    return pl.DataFrame({
        "ts": dates,
        "BTCUSDT-PERP": [float(40000 + i * 100) for i in range(10)],
    })


def _make_fwd_df(panel: "pl.DataFrame") -> "pl.DataFrame":
    """Minimal forward-returns frame (1-period pct change, drop first row)."""
    col = panel["BTCUSDT-PERP"]
    fwd = col.pct_change().slice(1)
    ts = panel["ts"].slice(1)
    return pl.DataFrame({"ts": ts, "BTCUSDT-PERP": fwd})


# ---------------------------------------------------------------------------
# 1. 404 when factor not in registry
# ---------------------------------------------------------------------------

def test_params_grid_404_unknown_factor(client, tmp_path):
    """``/params_grid`` returns 404 when factor_name is not registered."""
    from tinohelm.core.paths import paths as _paths

    empty_dir = tmp_path / "empty_factors"
    empty_dir.mkdir()
    _paths.override("factors_dir", empty_dir)
    try:
        resp = client.post(
            "/api/factor/params_grid",
            json={
                "factor_name": "nonexistent_factor_xyz",
                "grid": {"n": [5, 10]},
                "start": "2026-01-01",
                "end": "2026-04-01",
                "universe": ["BTCUSDT-PERP"],
            },
        )
    finally:
        _paths.reset_overrides()

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 2. 422 when universe is empty
# ---------------------------------------------------------------------------

def test_params_grid_422_empty_universe(client, tmp_path, mock_factor_spec):
    """``/params_grid`` returns 422 when universe is empty."""
    mock_registry = MagicMock()
    mock_registry.get_spec.return_value = mock_factor_spec
    mock_registry.get_kernel.return_value = lambda **kw: None

    with patch("tinohelm.factor.registry.Registry", return_value=mock_registry):
        resp = client.post(
            "/api/factor/params_grid",
            json={
                "factor_name": "ret_N",
                "grid": {"n": [5, 10]},
                "start": "2026-01-01",
                "end": "2026-04-01",
                "universe": [],  # empty
            },
        )

    assert resp.status_code == 422
    assert "universe" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 3. DataLayer.load is called with list[DataRequest], not str
# ---------------------------------------------------------------------------

def test_params_grid_calls_data_layer_with_data_requests(
    client, mock_factor_spec, mock_close_panel
):
    """``DataLayer.load`` must receive a list of DataRequest objects, not a str."""
    from tinohelm.factor.types import DataRequest

    captured_args: list = []

    def fake_data_layer_load(request, start=None, end=None):
        captured_args.append(request)
        # Return a dict keyed by field_name — same as real DataLayer.
        return {"close": mock_close_panel}

    mock_registry = MagicMock()
    mock_registry.get_spec.return_value = mock_factor_spec
    # Kernel returns the panel unchanged (params_grid will compute IC from it)
    mock_registry.get_kernel.return_value = lambda close, params: close

    # Minimal forward-returns and params_grid stubs so the test does not
    # require real evaluator I/O.
    fake_fwd = _make_fwd_df(mock_close_panel)

    mock_params_grid_result = [
        {"params": {"n": 5}, "ic_mean": 0.03, "ir": 0.5, "ic_series": [], "panel": None},
    ]

    with (
        patch("tinohelm.factor.registry.Registry", return_value=mock_registry),
        patch(
            "tinohelm.factor.data_layer.DataLayer.load",
            side_effect=fake_data_layer_load,
        ),
        patch(
            "tinohelm.factor.evaluation.evaluator._to_ts_value",
            return_value=fake_fwd,
        ),
        patch(
            "tinohelm.factor.evaluation.ic.forward_returns",
            return_value=fake_fwd,
        ),
        patch(
            "tinohelm.factor.evaluation.params_grid.params_grid",
            return_value=mock_params_grid_result,
        ),
    ):
        resp = client.post(
            "/api/factor/params_grid",
            json={
                "factor_name": "ret_N",
                "grid": {"n": [5, 10]},
                "start": "2026-01-01",
                "end": "2026-04-01",
                "universe": ["BTCUSDT-PERP"],
            },
        )

    assert resp.status_code == 200, resp.text

    # DataLayer.load must have been called with a list of DataRequest objects.
    assert len(captured_args) == 1
    loaded_request = captured_args[0]
    assert isinstance(loaded_request, list), (
        f"DataLayer.load received {type(loaded_request)!r} instead of list[DataRequest]; "
        "this is the B1 regression — must not pass str or EvalConfig"
    )
    assert len(loaded_request) > 0
    assert all(isinstance(r, DataRequest) for r in loaded_request)

    # Response shape is correct.
    body = resp.json()
    assert body["factor_name"] == "ret_N"
    assert "candidates" in body
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["params"] == {"n": 5}


def test_params_grid_passes_declared_non_close_inputs(client, mock_close_panel):
    """Kernel kwargs follow FactorSpec.input_specs instead of hard-coded close."""
    volume_panel = mock_close_panel.with_columns(pl.lit(1000.0).alias("BTCUSDT-PERP"))
    captured_requests: list = []
    spec = MagicMock()
    spec.params = {"lookback": 5}
    spec.lookback = 5
    inp = MagicMock()
    inp.field_name = "volume"
    inp.frequency = None
    spec.input_specs = [inp]

    captured_kwargs: dict = {}

    def _kernel(**kwargs):
        captured_kwargs.update(kwargs)
        return kwargs["volume"]

    mock_registry = MagicMock()
    mock_registry.get_spec.return_value = spec
    mock_registry.get_kernel.return_value = _kernel
    fake_fwd = _make_fwd_df(mock_close_panel)
    mock_params_grid_result = [
        {"params": {"lookback": 5}, "ic_mean": 0.03, "ir": 0.5},
    ]

    def _fake_load(request, start=None, end=None):
        captured_requests.extend(request)
        return {"volume": volume_panel, "close": mock_close_panel}

    with (
        patch("tinohelm.factor.registry.Registry", return_value=mock_registry),
        patch(
            "tinohelm.factor.data_layer.DataLayer.load",
            side_effect=_fake_load,
        ),
        patch(
            "tinohelm.factor.evaluation.evaluator._to_ts_value",
            return_value=fake_fwd,
        ),
        patch(
            "tinohelm.factor.evaluation.ic.forward_returns",
            return_value=fake_fwd,
        ),
        patch(
            "tinohelm.factor.evaluation.params_grid.params_grid",
            side_effect=lambda factor_fn, *args, **kwargs: (
                factor_fn(lookback=5),
                mock_params_grid_result,
            )[1],
        ),
    ):
        resp = client.post(
            "/api/factor/params_grid",
            json={
                "factor_name": "volume_factor",
                "grid": {"lookback": [5]},
                "start": "2026-01-01",
                "end": "2026-04-01",
                "universe": ["BTCUSDT-PERP"],
            },
        )

    assert resp.status_code == 200, resp.text
    assert {r.field_name for r in captured_requests} == {"volume", "close"}
    assert "volume" in captured_kwargs
    assert "close" not in captured_kwargs
    assert captured_kwargs["volume"] is volume_panel


def test_params_grid_requires_close_eval_target(client, mock_close_panel):
    """Non-close factors must not evaluate IC against their own input panel."""
    volume_panel = mock_close_panel.with_columns(pl.lit(1000.0).alias("BTCUSDT-PERP"))
    spec = MagicMock()
    spec.params = {"lookback": 5}
    spec.lookback = 5
    inp = MagicMock()
    inp.field_name = "volume"
    inp.frequency = None
    spec.input_specs = [inp]

    mock_registry = MagicMock()
    mock_registry.get_spec.return_value = spec
    mock_registry.get_kernel.return_value = lambda volume, params: volume

    with (
        patch("tinohelm.factor.registry.Registry", return_value=mock_registry),
        patch(
            "tinohelm.factor.data_layer.DataLayer.load",
            return_value={"volume": volume_panel},
        ),
    ):
        resp = client.post(
            "/api/factor/params_grid",
            json={
                "factor_name": "volume_factor",
                "grid": {"lookback": [5]},
                "start": "2026-01-01",
                "end": "2026-04-01",
                "universe": ["BTCUSDT-PERP"],
            },
        )

    assert resp.status_code == 400
    assert "close price panel" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 4. Response shape validation
# ---------------------------------------------------------------------------

def test_params_grid_response_shape(client, mock_factor_spec, mock_close_panel):
    """Successful call returns ``{factor_name, candidates}`` with correct fields."""
    mock_registry = MagicMock()
    mock_registry.get_spec.return_value = mock_factor_spec
    mock_registry.get_kernel.return_value = lambda close, params: close

    fake_fwd = _make_fwd_df(mock_close_panel)
    mock_candidates = [
        {"params": {"n": 10}, "ic_mean": 0.05, "ir": 0.8, "ic_series": [], "panel": None},
        {"params": {"n": 20}, "ic_mean": 0.04, "ir": 0.7, "ic_series": [], "panel": None},
    ]

    with (
        patch("tinohelm.factor.registry.Registry", return_value=mock_registry),
        patch(
            "tinohelm.factor.data_layer.DataLayer.load",
            return_value={"close": mock_close_panel},
        ),
        patch(
            "tinohelm.factor.evaluation.evaluator._to_ts_value",
            return_value=fake_fwd,
        ),
        patch(
            "tinohelm.factor.evaluation.ic.forward_returns",
            return_value=fake_fwd,
        ),
        patch(
            "tinohelm.factor.evaluation.params_grid.params_grid",
            return_value=mock_candidates,
        ),
    ):
        resp = client.post(
            "/api/factor/params_grid",
            json={
                "factor_name": "ret_N",
                "grid": {"n": [10, 20]},
                "top_k": 2,
                "start": "2026-01-01",
                "end": "2026-04-01",
                "universe": ["BTCUSDT-PERP"],
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["factor_name"] == "ret_N"
    candidates = body["candidates"]
    assert len(candidates) == 2
    for c in candidates:
        assert "params" in c
        assert "ic_mean" in c
        assert "ir" in c
