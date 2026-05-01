from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinohelm.api.routes import factor as factor_routes
from tinohelm.factor.decorator import factor
from tinohelm.factor.registry import Registry
from tinohelm.factor.types import EvalConfig, FactorSpec, Panel
from tinohelm.factor.worker import _queue_payload_from_run


def test_registry_exposes_source_debug_metadata_for_user_factor(tmp_path: Path):
    factor_file = tmp_path / "custom_alpha.py"
    factor_file.write_text(
        "from tinohelm.factor.decorator import factor\n"
        "from tinohelm.factor.types import Panel\n"
        "@factor(category='test', lookback=1)\n"
        "def custom_alpha(close: Panel) -> Panel:\n"
        "    return close\n",
        encoding="utf-8",
    )

    registry = Registry(user_dir=tmp_path, builtins_package="does.not.exist")
    spec = registry.scan()["custom_alpha"]
    item = factor_routes._spec_to_dict(spec)

    assert item["source_file"] == str(factor_file)
    assert item["module_path"].endswith("custom_alpha")
    assert item["code_hash"]


def test_parse_eval_config_rejects_unknown_segment_provider():
    with pytest.raises(ValueError) as exc:
        factor_routes._parse_and_validate_config(
            {"universe": ["BTCUSDT-PERP"], "start": "2024-01-01", "end": "2024-01-02", "segments": ["bogus"]},
            params=None,
        )

    payload = exc.value.args[0]
    assert payload["code"] == "unknown_segment_provider"
    assert payload["invalid_values"] == ["bogus"]
    assert "btc_trend" in payload["valid_values"]


def test_report_payload_marks_failed_job_status_explicitly():
    payload = factor_routes._report_payload(
        run_id="r1",
        factor_name="alpha",
        status="failed",
        progress=100,
        error="Traceback... boom",
        result=None,
    )

    assert payload["status"] == "failed"
    assert payload["error_code"] == "factor_run_failed"
    assert payload["job_ok"] is False
    assert "boom" in payload["message"]


def test_report_payload_summary_omits_large_detail_arrays():
    payload = factor_routes._report_payload(
        run_id="r1",
        factor_name="alpha",
        status="completed",
        progress=100,
        error=None,
        result={
            "ic_mean": 0.12,
            "ir": 1.5,
            "ic_tstat": 2.1,
            "rating": 3,
            "effective_params": {"lookback": 5},
            "cache_key": "abc123",
            "cache_hit": False,
            "factor_code_hash": "codehash",
            "factor_source_file": "/tmp/alpha.py",
            "factor_module_path": "alpha.alpha",
            "distribution_histogram": [{"bin": i} for i in range(50)],
            "warnings": [],
        },
        summary=True,
        detail=False,
        fields=["ic_mean", "ir", "rating"],
    )

    assert payload["status"] == "completed"
    assert payload["summary"] == {"ic_mean": 0.12, "ir": 1.5, "rating": 3}
    assert payload["meta"]["cache_key"] == "abc123"
    assert payload["meta"]["factor_code_hash"] == "codehash"
    assert "result" not in payload


def test_effective_params_merge_defaults_and_overrides():
    spec = FactorSpec(name="alpha", category="test", params={"lookback": 20, "z": 1})
    config = EvalConfig(universe=("BTC",), start="2024-01-01", end="2024-01-02", params={"lookback": 5})

    assert factor_routes._effective_params(spec, config) == {"lookback": 5, "z": 1}


def test_vwap_proxy_metadata_is_llm_visible():
    @factor(category="test", lookback=1)
    def vwap_mid_reversion_proxy(close: Panel) -> Panel:
        return close

    item = factor_routes._spec_to_dict(vwap_mid_reversion_proxy.__factor_spec__)

    assert item["metadata"]["proxy_level"] == "1m_ohlcv_bar_proxy"
    assert item["warnings"][0]["code"] == "research_proxy_not_execution_premium"


def test_recovered_factor_run_requeues_full_payload_not_bare_id():
    run = SimpleNamespace(
        id="run-1",
        factor_name="alpha",
        config={
            "universe": ["BTCUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-01-02",
            "params": {"lookback": 5},
            "_tino_run_options": {"full": True},
        },
    )

    payload = json.loads(_queue_payload_from_run(run))

    assert payload["run_id"] == "run-1"
    assert payload["factor_name"] == "alpha"
    assert payload["config"]["params"] == {"lookback": 5}
    assert payload["params"] == {"lookback": 5}
    assert payload["full"] is True
