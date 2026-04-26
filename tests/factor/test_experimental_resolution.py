"""Tests for experimental factor handling.

Validates AC#2:
- 3 experimental factors (trade_imbalance, oi_change, orderbook_imbalance_L1)
  are marked deprecated=True and experimental=True in the registry.
- /api/factor/list default endpoint does NOT return these factors.
- Calling their kernels raises NotImplementedError.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Test: experimental factors are registered with deprecated=True
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("factor_name", [
    "trade_imbalance",
    "oi_change",
    "orderbook_imbalance_L1",
])
def test_experimental_factor_marked_deprecated(factor_name: str):
    """Each experimental factor has both deprecated=True and experimental=True."""
    from tinohelm.factor.registry import Registry

    registry = Registry()
    registry.scan()

    spec = registry.get_spec(factor_name)
    assert spec is not None, (
        f"Factor {factor_name!r} not found in registry after scan()"
    )
    assert spec.deprecated is True, (
        f"{factor_name}: expected deprecated=True, got {spec.deprecated!r}"
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
# Test: invoking experimental factor kernels raises NotImplementedError
# ---------------------------------------------------------------------------

def test_trade_imbalance_raises():
    """Calling trade_imbalance kernel raises NotImplementedError."""
    from tinohelm.factor.builtins.microstructure import trade_imbalance

    with pytest.raises(NotImplementedError, match="trade_imbalance"):
        trade_imbalance(None, None)


def test_oi_change_raises():
    """Calling oi_change kernel raises NotImplementedError."""
    from tinohelm.factor.builtins.crypto_data import oi_change

    with pytest.raises(NotImplementedError, match="oi_change"):
        oi_change(None)


def test_orderbook_imbalance_l1_raises():
    """Calling orderbook_imbalance_L1 kernel raises NotImplementedError."""
    from tinohelm.factor.builtins.crypto_data import orderbook_imbalance_L1

    with pytest.raises(NotImplementedError, match="orderbook_imbalance_L1"):
        orderbook_imbalance_L1(None)
