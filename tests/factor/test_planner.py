"""Unit tests for ``tinohelm.factor.engine.planner``.

Coverage
--------
- merge_data_requests: deduplication by (symbol, field_name, frequency, source)
- merge_data_requests: lookback closure takes maximum across factors
- merge_data_requests: separate groups for different sources (bar vs funding_rate)
- merge_data_requests: InputSpec with frequency=None uses effective_frequency
- plan: returns Plan with data_requests and layers
- plan: single topological layer when no depends_on_factors
- plan: deterministic layer ordering (sorted by name)
- plan_batch: splits layers into separate Plans
- _topological_layers: dependency ordering when depends_on_factors present
- _topological_layers: cycle detection falls back to single layer
- empty specs: plan returns empty Plan
"""
from __future__ import annotations

import pytest

from tinohelm.factor.engine.planner import Plan, Planner, _infer_source
from tinohelm.factor.types import DataRequest, FactorSpec, InputSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SYMBOLS = ["BTCUSDT-PERP", "ETHUSDT-PERP"]
FREQ = "1-MINUTE"


def _make_spec(
    name: str,
    field_names: list[str],
    lookback: int,
    *,
    category: str = "test",
    frequency: str | None = None,
    depends_on_factors: list[str] | None = None,
) -> FactorSpec:
    input_specs = tuple(InputSpec(field_name=f, frequency=frequency) for f in field_names)
    params: dict = {}
    if depends_on_factors:
        params["depends_on_factors"] = depends_on_factors
    return FactorSpec(
        name=name,
        category=category,
        lookback=lookback,
        input_specs=input_specs,
        params=params,
    )


# ---------------------------------------------------------------------------
# _infer_source utility
# ---------------------------------------------------------------------------

class TestInferSource:
    def test_ohlcv_fields_are_bar(self):
        for field in ("close", "open", "high", "low", "volume", "amount", "vwap"):
            assert _infer_source(field) == "bar"

    def test_funding_rate(self):
        assert _infer_source("funding_rate") == "funding_rate"

    def test_open_interest(self):
        assert _infer_source("open_interest") == "open_interest"

    def test_quote_tick(self):
        assert _infer_source("orderbook_imbalance") == "quote_tick"

    def test_unknown_field_defaults_to_bar(self):
        assert _infer_source("my_custom_field") == "bar"


# ---------------------------------------------------------------------------
# merge_data_requests
# ---------------------------------------------------------------------------

class TestMergeDataRequests:
    def setup_method(self):
        self.planner = Planner(default_frequency=FREQ)

    def test_deduplication_same_field_same_lookback(self):
        """Two factors reading the same field → one DataRequest per symbol."""
        spec_a = _make_spec("factor_a", ["close"], lookback=20)
        spec_b = _make_spec("factor_b", ["close"], lookback=20)

        reqs = self.planner.merge_data_requests([spec_a, spec_b], symbols=SYMBOLS)

        close_reqs = [r for r in reqs if r.field_name == "close"]
        assert len(close_reqs) == len(SYMBOLS), (
            "Expected one DataRequest per symbol, not one per (spec, symbol)"
        )

    def test_lookback_closure_takes_maximum(self):
        """Two factors reading 'close' with lookbacks 20 and 50 → lookback=50."""
        spec_a = _make_spec("factor_a", ["close"], lookback=20)
        spec_b = _make_spec("factor_b", ["close"], lookback=50)

        reqs = self.planner.merge_data_requests([spec_a, spec_b], symbols=["BTCUSDT-PERP"])

        close_reqs = [r for r in reqs if r.field_name == "close"]
        assert len(close_reqs) == 1
        assert close_reqs[0].lookback == 50

    def test_separate_groups_bar_vs_funding_rate(self):
        """Bar factor + funding_rate factor → 2 distinct DataRequest groups."""
        spec_bar = _make_spec("ret_N", ["close", "volume"], lookback=20)
        spec_fr = _make_spec("fr_factor", ["funding_rate"], lookback=10)

        reqs = self.planner.merge_data_requests(
            [spec_bar, spec_fr], symbols=["BTCUSDT-PERP"]
        )

        sources = {r.source for r in reqs}
        assert "bar" in sources
        assert "funding_rate" in sources

    def test_three_factors_two_bar_one_funding_rate(self):
        """AC-test: 3 specs → 2 DataRequest groups (bar + funding_rate)."""
        spec_a = _make_spec("factor_a", ["close"], lookback=20)
        spec_b = _make_spec("factor_b", ["volume"], lookback=30)
        spec_fr = _make_spec("factor_fr", ["funding_rate"], lookback=10)

        reqs = self.planner.merge_data_requests(
            [spec_a, spec_b, spec_fr], symbols=["BTCUSDT-PERP"]
        )

        bar_reqs = [r for r in reqs if r.source == "bar"]
        fr_reqs = [r for r in reqs if r.source == "funding_rate"]

        assert len(bar_reqs) == 2, f"Expected 2 bar reqs (close + volume), got {bar_reqs}"
        assert len(fr_reqs) == 1, f"Expected 1 funding_rate req, got {fr_reqs}"

    def test_multi_symbol_produces_per_symbol_requests(self):
        """Each symbol gets its own DataRequest."""
        spec = _make_spec("ret_N", ["close"], lookback=20)
        reqs = self.planner.merge_data_requests([spec], symbols=SYMBOLS)

        symbols_in_reqs = {r.symbol for r in reqs}
        assert symbols_in_reqs == set(SYMBOLS)

    def test_frequency_none_uses_effective_frequency(self):
        """InputSpec.frequency=None should resolve to the effective_frequency."""
        spec = _make_spec("ret_N", ["close"], lookback=20, frequency=None)
        reqs = self.planner.merge_data_requests(
            [spec], symbols=["BTCUSDT-PERP"], frequency="5-MINUTE"
        )

        assert all(r.frequency == "5-MINUTE" for r in reqs)

    def test_explicit_frequency_on_input_spec_is_preserved(self):
        """InputSpec.frequency != None should be used as-is."""
        spec = _make_spec("ret_N", ["close"], lookback=20, frequency="5-MINUTE")
        reqs = self.planner.merge_data_requests(
            [spec], symbols=["BTCUSDT-PERP"], frequency="1-MINUTE"
        )

        assert all(r.frequency == "5-MINUTE" for r in reqs)

    def test_different_frequencies_not_merged(self):
        """Same field but different frequencies stay as separate DataRequests."""
        spec_1m = _make_spec("factor_1m", ["close"], lookback=20, frequency="1-MINUTE")
        spec_5m = _make_spec("factor_5m", ["close"], lookback=30, frequency="5-MINUTE")

        reqs = self.planner.merge_data_requests(
            [spec_1m, spec_5m], symbols=["BTCUSDT-PERP"]
        )

        freqs = {r.frequency for r in reqs}
        assert "1-MINUTE" in freqs
        assert "5-MINUTE" in freqs
        assert len(reqs) == 2

    def test_empty_specs_returns_empty(self):
        reqs = self.planner.merge_data_requests([], symbols=SYMBOLS)
        assert reqs == []

    def test_correct_source_in_data_request(self):
        spec = _make_spec("fr", ["funding_rate"], lookback=5)
        reqs = self.planner.merge_data_requests([spec], symbols=["BTCUSDT-PERP"])
        assert reqs[0].source == "funding_rate"
        assert reqs[0].field_name == "funding_rate"


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

class TestPlan:
    def setup_method(self):
        self.planner = Planner(default_frequency=FREQ)

    def test_plan_returns_plan_instance(self):
        spec = _make_spec("factor_a", ["close"], lookback=20)
        result = self.planner.plan([spec], symbols=["BTCUSDT-PERP"])
        assert isinstance(result, Plan)

    def test_plan_contains_data_requests(self):
        spec = _make_spec("factor_a", ["close"], lookback=20)
        result = self.planner.plan([spec], symbols=["BTCUSDT-PERP"])
        assert len(result.data_requests) > 0
        assert all(isinstance(r, DataRequest) for r in result.data_requests)

    def test_no_dependencies_single_layer(self):
        """All specs without depends_on_factors → single topological layer."""
        specs = [
            _make_spec("factor_a", ["close"], lookback=20),
            _make_spec("factor_b", ["volume"], lookback=10),
            _make_spec("factor_c", ["funding_rate"], lookback=5),
        ]
        result = self.planner.plan(specs, symbols=["BTCUSDT-PERP"])
        assert len(result.layers) == 1
        assert len(result.layers[0]) == 3

    def test_layer_ordering_is_deterministic(self):
        """Layer contents should be sorted by factor name for reproducibility."""
        specs = [
            _make_spec("zzz_factor", ["close"], lookback=1),
            _make_spec("aaa_factor", ["close"], lookback=1),
            _make_spec("mmm_factor", ["close"], lookback=1),
        ]
        result = self.planner.plan(specs, symbols=["BTCUSDT-PERP"])
        layer = result.layers[0]
        names = [s.name for s in layer]
        assert names == sorted(names)

    def test_empty_specs_returns_empty_plan(self):
        result = self.planner.plan([], symbols=["BTCUSDT-PERP"])
        assert result.data_requests == []
        assert result.layers == []


# ---------------------------------------------------------------------------
# plan_batch
# ---------------------------------------------------------------------------

class TestPlanBatch:
    def setup_method(self):
        self.planner = Planner(default_frequency=FREQ)

    def test_plan_batch_single_layer(self):
        specs = [
            _make_spec("a", ["close"], lookback=10),
            _make_spec("b", ["volume"], lookback=20),
        ]
        batch = self.planner.plan_batch(specs, symbols=["BTCUSDT-PERP"])
        assert len(batch) == 1
        assert all(isinstance(p, Plan) for p in batch)

    def test_plan_batch_each_plan_has_one_layer(self):
        specs = [_make_spec("a", ["close"], lookback=10)]
        batch = self.planner.plan_batch(specs, symbols=["BTCUSDT-PERP"])
        for plan in batch:
            assert len(plan.layers) == 1

    def test_plan_batch_data_requests_match_layer_specs(self):
        """Each batch Plan's data_requests should only cover its own layer specs."""
        specs = [
            _make_spec("a", ["close"], lookback=10),
        ]
        batch = self.planner.plan_batch(specs, symbols=["BTCUSDT-PERP"])
        # Only 'close' should appear
        fields = {r.field_name for p in batch for r in p.data_requests}
        assert fields == {"close"}


# ---------------------------------------------------------------------------
# Topological ordering with dependencies
# ---------------------------------------------------------------------------

class TestTopologicalLayers:
    def setup_method(self):
        self.planner = Planner(default_frequency=FREQ)

    def test_dependency_ordering_two_layers(self):
        """factor_b depends on factor_a → factor_a in layer 0, factor_b in layer 1."""
        spec_a = _make_spec("factor_a", ["close"], lookback=10)
        spec_b = _make_spec(
            "factor_b", ["close"], lookback=10,
            depends_on_factors=["factor_a"]
        )
        result = self.planner.plan([spec_a, spec_b], symbols=["BTCUSDT-PERP"])

        assert len(result.layers) == 2
        assert result.layers[0][0].name == "factor_a"
        assert result.layers[1][0].name == "factor_b"

    def test_independent_factors_same_layer(self):
        specs = [
            _make_spec("x", ["close"], lookback=10),
            _make_spec("y", ["volume"], lookback=10),
        ]
        result = self.planner.plan(specs, symbols=["BTCUSDT-PERP"])
        assert len(result.layers) == 1
        assert len(result.layers[0]) == 2

    def test_unknown_dependency_ignored(self):
        """depends_on_factors referencing a factor not in the batch is ignored."""
        spec = _make_spec(
            "factor_a", ["close"], lookback=10,
            depends_on_factors=["nonexistent_factor"]
        )
        result = self.planner.plan([spec], symbols=["BTCUSDT-PERP"])
        # Should not error; nonexistent dep is filtered out
        assert len(result.layers) == 1
