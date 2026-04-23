"""DAG Planner for the declarative factor framework.

Responsibilities
----------------
1. **Data request merging**: Given a list of FactorSpecs, collect all
   InputSpecs and produce the minimal set of DataRequests by:
   - Grouping by (symbol, field_name, frequency, source)
   - Taking the maximum lookback within each group (lookback closure)

2. **Topological sort**: Order factor groups by dependency.  In the current
   version FactorSpec has no ``depends_on_factors`` field, so all specs are
   placed in a single layer (layer 0).  The extension point reads
   ``spec.params.get("depends_on_factors", [])`` to support future
   inter-factor dependencies without schema change.

3. **Plan construction**: Combine merged DataRequests + sorted factor groups
   into a ``Plan`` dataclass consumed by ``Scheduler``.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from tinohelm.factor.types import DataRequest, FactorSpec


# ---------------------------------------------------------------------------
# Plan — output of Planner.plan()
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    """Output of :meth:`Planner.plan`.

    Attributes
    ----------
    data_requests:
        Deduplicated, lookback-closed list of DataRequests that must be
        loaded before Scheduler can execute the plan.
    layers:
        Topologically sorted list of factor groups.  Each group (inner list)
        contains specs that can be executed in parallel.  Groups must be
        executed sequentially: layer[0] before layer[1], etc.
    """

    data_requests: list[DataRequest] = field(default_factory=list)
    layers: list[list[FactorSpec]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

class Planner:
    """Computes data requirements and execution order for a set of FactorSpecs.

    Parameters
    ----------
    default_frequency:
        Frequency string used when an ``InputSpec`` has ``frequency=None``.
        Defaults to ``"1-MINUTE"``.
    default_symbols:
        Symbol list used when building DataRequests if no symbol context is
        provided.  Defaults to an empty list (callers must supply symbols via
        ``plan()``).
    """

    def __init__(
        self,
        default_frequency: str = "1-MINUTE",
        default_symbols: list[str] | None = None,
    ) -> None:
        self._default_frequency = default_frequency
        self._default_symbols: list[str] = default_symbols or []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        specs: list[FactorSpec],
        symbols: list[str] | None = None,
        frequency: str | None = None,
    ) -> Plan:
        """Build a :class:`Plan` from a list of :class:`FactorSpec` objects.

        Parameters
        ----------
        specs:
            Factor specifications to plan.
        symbols:
            Symbol universe.  Falls back to ``self._default_symbols``.
        frequency:
            Bar frequency override.  Falls back to ``self._default_frequency``.

        Returns
        -------
        Plan
            Merged DataRequests + topologically sorted factor layers.
        """
        effective_symbols = symbols if symbols is not None else self._default_symbols
        effective_freq = frequency if frequency is not None else self._default_frequency

        data_requests = self.merge_data_requests(
            specs, symbols=effective_symbols, frequency=effective_freq
        )
        layers = self._topological_layers(specs)
        return Plan(data_requests=data_requests, layers=layers)

    def plan_batch(
        self,
        specs: list[FactorSpec],
        symbols: list[str] | None = None,
        frequency: str | None = None,
    ) -> list[Plan]:
        """Produce one :class:`Plan` per topological layer.

        This is a convenience wrapper: ``plan()`` already contains all layers
        in one Plan; this method splits them so each Plan covers exactly one
        layer.  Useful when the caller wants to execute layers one at a time
        with independent data loading.

        Parameters
        ----------
        specs:
            Factor specifications to plan.
        symbols:
            Symbol universe.
        frequency:
            Bar frequency override.

        Returns
        -------
        list[Plan]
            One Plan per topological layer, in execution order.
        """
        full_plan = self.plan(specs, symbols=symbols, frequency=frequency)
        result: list[Plan] = []
        for layer in full_plan.layers:
            layer_data_requests = self.merge_data_requests(
                layer, symbols=symbols, frequency=frequency
            )
            result.append(Plan(data_requests=layer_data_requests, layers=[layer]))
        return result

    def merge_data_requests(
        self,
        specs: list[FactorSpec],
        symbols: list[str] | None = None,
        frequency: str | None = None,
    ) -> list[DataRequest]:
        """Merge InputSpecs from all specs into deduplicated DataRequests.

        Merging rules
        -------------
        - Group by ``(symbol, field_name, frequency, source)``.
        - Within each group take ``max(lookback)`` (lookback closure).
        - Source is inferred from field_name: ``"funding_rate"`` →
          ``"funding_rate"``, ``"open_interest"`` → ``"open_interest"``,
          all OHLCV/vwap fields → ``"bar"``.
        - DataRequests with ``frequency=None`` are given the effective
          ``frequency`` parameter (or ``self._default_frequency``).

        Parameters
        ----------
        specs:
            Factor specs to aggregate.
        symbols:
            Symbol universe.  Falls back to ``self._default_symbols``.
        frequency:
            Bar frequency override.  Falls back to ``self._default_frequency``.

        Returns
        -------
        list[DataRequest]
            Sorted list of deduplicated DataRequests.
        """
        effective_symbols = symbols if symbols is not None else self._default_symbols
        effective_freq = frequency if frequency is not None else self._default_frequency

        # Key: (symbol, field_name, resolved_frequency, source) → max lookback
        merged: dict[tuple[str, str, str, str], int] = defaultdict(int)

        for spec in specs:
            for inp in spec.input_specs:
                resolved_freq = inp.frequency if inp.frequency is not None else effective_freq
                source = _infer_source(inp.field_name)
                lookback = spec.lookback

                if effective_symbols:
                    for sym in effective_symbols:
                        key = (sym, inp.field_name, resolved_freq, source)
                        merged[key] = max(merged[key], lookback)
                else:
                    # No symbols: use empty string as placeholder symbol
                    key = ("", inp.field_name, resolved_freq, source)
                    merged[key] = max(merged[key], lookback)

        requests: list[DataRequest] = [
            DataRequest(
                symbol=sym,
                field_name=field_name,
                frequency=freq,
                lookback=lookback,
                source=source,
            )
            for (sym, field_name, freq, source), lookback in sorted(merged.items())
        ]
        return requests

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _topological_layers(self, specs: list[FactorSpec]) -> list[list[FactorSpec]]:
        """Return specs grouped into topological layers.

        Current version: all specs have no declared inter-factor dependencies
        so they all land in layer 0.

        Extension point: if ``spec.params`` contains ``"depends_on_factors"``
        (a list of factor names), those names are treated as dependencies and
        the Kahn BFS algorithm produces the correct layer ordering.

        Parameters
        ----------
        specs:
            Factor specs to sort.

        Returns
        -------
        list[list[FactorSpec]]
            Topologically sorted layers.  Each inner list can run in parallel.
        """
        if not specs:
            return []

        # Build name → spec mapping
        name_to_spec: dict[str, FactorSpec] = {s.name: s for s in specs}

        # Build dependency graph
        # dependencies[name] = set of names this spec depends on
        dependencies: dict[str, set[str]] = {}
        for spec in specs:
            deps_raw: list[Any] = spec.params.get("depends_on_factors", [])
            # Filter to only deps that are part of this batch
            deps_in_batch = {d for d in deps_raw if d in name_to_spec}
            dependencies[spec.name] = deps_in_batch

        # Kahn's algorithm for topological layering
        # in_degree[n] = number of specs n depends on (0 = ready to execute)
        in_degree = {s.name: len(dependencies[s.name]) for s in specs}

        layers: list[list[FactorSpec]] = []
        remaining = set(s.name for s in specs)

        while remaining:
            # Specs whose dependencies are all resolved (in_degree == 0)
            current_layer_names = {
                name for name in remaining if in_degree[name] == 0
            }
            if not current_layer_names:
                raise ValueError(
                    f"Cyclic dependency detected among factors: "
                    f"{sorted(remaining)}"
                )

            # Sort for deterministic ordering
            current_layer = [
                name_to_spec[name]
                for name in sorted(current_layer_names)
            ]
            layers.append(current_layer)
            remaining -= current_layer_names

            # Reduce in_degree for specs that depended on the just-resolved layer
            for name in remaining:
                resolved_deps = dependencies[name] & current_layer_names
                in_degree[name] -= len(resolved_deps)

        return layers


# ---------------------------------------------------------------------------
# Internal utility
# ---------------------------------------------------------------------------

_FUNDING_RATE_FIELDS: frozenset[str] = frozenset({"funding_rate", "mark_price"})
_OPEN_INTEREST_FIELDS: frozenset[str] = frozenset({
    "open_interest", "sum_open_interest", "open_interest_value",
})
_TRADE_TICK_FIELDS: frozenset[str] = frozenset({
    "trade_tick", "trade_price", "trade_qty", "trade_side",
})
_QUOTE_TICK_FIELDS: frozenset[str] = frozenset({
    "quote_tick", "orderbook_imbalance", "bid_price", "bid_qty", "ask_price", "ask_qty",
})


def _infer_source(field_name: str) -> str:
    """Infer the DataRequest ``source`` from the canonical field name."""
    if field_name in _FUNDING_RATE_FIELDS:
        return "funding_rate"
    if field_name in _OPEN_INTEREST_FIELDS:
        return "open_interest"
    if field_name in _TRADE_TICK_FIELDS:
        return "trade_tick"
    if field_name in _QUOTE_TICK_FIELDS:
        return "quote_tick"
    return "bar"
