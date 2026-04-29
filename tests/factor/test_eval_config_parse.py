"""Regression tests for shared factor EvalConfig parsing."""
from __future__ import annotations

from tinohelm.factor.config import parse_eval_config
from tinohelm.factor.types import WalkForwardSpec


def test_parse_eval_config_preserves_extension_fields() -> None:
    config = parse_eval_config(
        {
            "universe": ["BTCUSDT-PERP", "ETHUSDT-PERP"],
            "start": "2024-01-01",
            "end": "2024-04-01",
            "returns_kind": "forward_returns",
            "universe_id": 7,
            "neutralize": ["btc_beta"],
            "walk_forward": {
                "train_bars": 1000,
                "test_bars": 200,
                "embargo_bars": 5,
                "purge_bars": 3,
            },
            "segments": ["btc_trend", "vol_regime"],
            "params": {"lookback": 20},
        },
        params={"lookback": 30},
    )

    assert config.universe == ("BTCUSDT-PERP", "ETHUSDT-PERP")
    assert config.returns_kind == "forward_returns"
    assert config.universe_id == 7
    assert config.neutralize == ("btc_beta",)
    assert config.walk_forward == WalkForwardSpec(
        train_bars=1000,
        test_bars=200,
        embargo_bars=5,
        purge_bars=3,
    )
    assert config.segments == ("btc_trend", "vol_regime")
    assert config.params == {"lookback": 30}
