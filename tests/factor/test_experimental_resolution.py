"""Tests for experimental factor handling.

Validates experimental factor handling:
- 3 downloadable-data factors are marked experimental=True and deprecated=False.
- /api/factor/list default endpoint does NOT return these factors.
- Calling their kernels no longer raises pending-stub NotImplementedError.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Test: experimental factors are registered but not deprecated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("factor_name", [
    "trade_imbalance",
    "oi_change",
    "orderbook_imbalance_L1",
])
def test_experimental_factor_not_deprecated(factor_name: str):
    """Each downloadable-data factor is experimental but not a deprecated stub."""
    from tinohelm.factor.registry import Registry

    registry = Registry()
    registry.scan()

    spec = registry.get_spec(factor_name)
    assert spec is not None, (
        f"Factor {factor_name!r} not found in registry after scan()"
    )
    assert spec.deprecated is False, (
        f"{factor_name}: expected deprecated=False, got {spec.deprecated!r}"
    )
    assert spec.experimental is True, (
        f"{factor_name}: expected experimental=True, got {spec.experimental!r}"
    )


# ---------------------------------------------------------------------------
# Test: /api/factor/list does not return experimental factors by default
# ---------------------------------------------------------------------------

def test_experimental_factors_not_in_default_list():
    """/api/factor/list (no query param) excludes the 3 experimental factors."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from tinohelm.api.routes import factor as factor_module

    app = FastAPI()
    app.include_router(factor_module.router)
    client = TestClient(app)

    resp = client.get("/api/factor/list")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    factor_names = {f["name"] for f in resp.json()}

    experimental = {"trade_imbalance", "oi_change", "orderbook_imbalance_L1"}
    overlap = factor_names & experimental
    assert not overlap, (
        f"/api/factor/list must not return experimental factors, "
        f"but found: {overlap}"
    )


def test_experimental_factors_included_with_flag():
    """/api/factor/list?include_experimental=true returns the 3 experimental factors."""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from tinohelm.api.routes import factor as factor_module

    app = FastAPI()
    app.include_router(factor_module.router)
    client = TestClient(app)

    resp = client.get("/api/factor/list?include_experimental=true")
    assert resp.status_code == 200

    factor_names = {f["name"] for f in resp.json()}
    experimental = {"trade_imbalance", "oi_change", "orderbook_imbalance_L1"}
    assert experimental.issubset(factor_names), (
        f"With include_experimental=true, expected {experimental} in results, "
        f"missing: {experimental - factor_names}"
    )


# ---------------------------------------------------------------------------
# Test: invoking experimental factor kernels is supported
# ---------------------------------------------------------------------------

def test_trade_imbalance_kernel_runs():
    from tinohelm.factor.builtins.microstructure import trade_imbalance
    import polars as pl

    panel = pl.DataFrame({"ts": [1, 2], "BTC": [1.0, -1.0]})
    assert trade_imbalance(panel, params={"lookback": 1}).columns == ["ts", "BTC"]


def test_oi_change_kernel_runs():
    from tinohelm.factor.builtins.crypto_data import oi_change
    import polars as pl

    panel = pl.DataFrame({"ts": [1, 2], "BTC": [1.0, 2.0]})
    assert oi_change(panel).columns == ["ts", "BTC"]


def test_orderbook_imbalance_l1_kernel_runs():
    from tinohelm.factor.builtins.crypto_data import orderbook_imbalance_L1
    import polars as pl

    panel = pl.DataFrame({"ts": [1], "BTC": [0.5]})
    assert orderbook_imbalance_L1(panel).equals(panel)


def test_planner_infers_book_depth_source():
    from tinohelm.factor.engine.planner import _infer_source

    assert _infer_source("book_depth") == "book_depth"
    assert _infer_source("book_depth_notional") == "book_depth"
    assert _infer_source("depth") == "book_depth"
    assert _infer_source("notional") == "book_depth"
