"""Unit tests — ``tinohelm.factor.evaluation.clustering``.

Locks the AC-2.4.2 contract:

* ``hierarchical_cluster(corr, method="ward")`` linkage matrix is byte-
  level identical for the same input.
* The linkage matrix has the canonical ``(F-1, 4)`` scipy shape.
* ``cut_dendrogram`` produces a 1-D array with one cluster ID per leaf.

Pure-logic, deterministic, scipy-backed, < 1s total.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from tinohelm.factor.evaluation.clustering import (
    _ALLOWED_METHODS,
    cut_dendrogram,
    hierarchical_cluster,
)
from tinohelm.factor.evaluation.correlation import correlation_matrix_cross_section


# ──────────────────────────────────────────────────────────────────────
# Shared fixtures
# ──────────────────────────────────────────────────────────────────────


def _make_panel(values: np.ndarray) -> pl.DataFrame:
    T, N = values.shape
    start = dt.datetime(2024, 1, 1)
    ts = [start + dt.timedelta(hours=i) for i in range(T)]
    data = {"ts": ts}
    for j in range(N):
        data[f"s{j}"] = values[:, j].tolist()
    return pl.DataFrame(data)


def _build_random_corr(F: int, T: int = 50, N: int = 10, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    panels = {f"f{i}": _make_panel(rng.normal(0, 1, (T, N))) for i in range(F)}
    return correlation_matrix_cross_section(panels)


def _two_clusters_corr() -> pl.DataFrame:
    """Build a 4-factor correlation matrix with a clear two-cluster structure.

    factors {f0, f1} are perfectly correlated (each is a copy of base_a);
    factors {f2, f3} are perfectly correlated (each is a copy of base_b);
    base_a and base_b are independent. Hierarchical clustering should
    therefore split into {f0, f1} | {f2, f3} at the highest cut.
    """
    rng = np.random.default_rng(99)
    T, N = 80, 12
    base_a = rng.normal(0, 1, (T, N))
    base_b = rng.normal(0, 1, (T, N))
    panels = {
        "f0": _make_panel(base_a),
        "f1": _make_panel(base_a.copy()),
        "f2": _make_panel(base_b),
        "f3": _make_panel(base_b.copy()),
    }
    return correlation_matrix_cross_section(panels)


# ──────────────────────────────────────────────────────────────────────
# hierarchical_cluster
# ──────────────────────────────────────────────────────────────────────


class TestHierarchicalCluster:
    def test_linkage_matrix_shape_is_F_minus_1_by_4(self):
        corr = _build_random_corr(F=4)
        out = hierarchical_cluster(corr)
        Z = out["linkage_matrix"]
        assert Z.shape == (3, 4)
        assert Z.dtype == np.float64

    def test_returns_canonical_keys(self):
        corr = _build_random_corr(F=3)
        out = hierarchical_cluster(corr)
        assert set(out.keys()) == {"linkage_matrix", "labels", "method"}
        assert out["method"] == "ward"
        assert out["labels"] == ["f0", "f1", "f2"]

    def test_ward_method_default(self):
        corr = _build_random_corr(F=3)
        out = hierarchical_cluster(corr)
        assert out["method"] == "ward"

    def test_explicit_method_passes_through(self):
        corr = _build_random_corr(F=3)
        out_avg = hierarchical_cluster(corr, method="average")
        assert out_avg["method"] == "average"
        Z_ward = hierarchical_cluster(corr, method="ward")["linkage_matrix"]
        # Different methods should produce different distance values in Z.
        assert not np.allclose(Z_ward, out_avg["linkage_matrix"])

    def test_unknown_method_raises(self):
        corr = _build_random_corr(F=3)
        with pytest.raises(ValueError, match="unknown linkage method"):
            hierarchical_cluster(corr, method="bogus")

    def test_too_few_factors_raises(self):
        corr = _build_random_corr(F=1)
        with pytest.raises(ValueError, match="at least 2 factors"):
            hierarchical_cluster(corr)

    def test_deterministic_byte_equivalent(self):
        # AC-2.4.2 — linkage matrix byte-equal across two calls with same input.
        corr_a = _build_random_corr(F=5, T=50, N=8, seed=42)
        corr_b = _build_random_corr(F=5, T=50, N=8, seed=42)
        Z_a = hierarchical_cluster(corr_a)["linkage_matrix"]
        Z_b = hierarchical_cluster(corr_b)["linkage_matrix"]
        # Byte-level equality — strict on the underlying bytes buffer.
        assert Z_a.tobytes() == Z_b.tobytes()

    def test_two_cluster_structure_recovered(self):
        corr = _two_clusters_corr()
        out = hierarchical_cluster(corr)
        labels = cut_dendrogram(out["linkage_matrix"], n_clusters=2)
        # f0 and f1 should share a cluster; f2 and f3 should share the other.
        # Map each leaf label name → cluster id.
        leaf_names = out["labels"]
        cluster_of = dict(zip(leaf_names, labels.tolist()))
        assert cluster_of["f0"] == cluster_of["f1"]
        assert cluster_of["f2"] == cluster_of["f3"]
        assert cluster_of["f0"] != cluster_of["f2"]

    def test_allowed_methods_set_pinned(self):
        # Locks against accidental method allow-list expansion / shrinkage.
        assert _ALLOWED_METHODS == frozenset(
            {"single", "complete", "average", "weighted", "centroid", "median", "ward"}
        )


# ──────────────────────────────────────────────────────────────────────
# cut_dendrogram
# ──────────────────────────────────────────────────────────────────────


class TestCutDendrogram:
    def test_n_clusters_2_returns_two_unique_ids(self):
        corr = _two_clusters_corr()
        Z = hierarchical_cluster(corr)["linkage_matrix"]
        labels = cut_dendrogram(Z, n_clusters=2)
        assert labels.shape == (4,)
        assert set(labels.tolist()) == {1, 2}

    def test_n_clusters_4_assigns_each_leaf_unique_id(self):
        corr = _build_random_corr(F=4)
        Z = hierarchical_cluster(corr)["linkage_matrix"]
        labels = cut_dendrogram(Z, n_clusters=4)
        assert labels.shape == (4,)
        assert set(labels.tolist()) == {1, 2, 3, 4}

    def test_invalid_n_clusters_raises(self):
        corr = _build_random_corr(F=4)
        Z = hierarchical_cluster(corr)["linkage_matrix"]
        with pytest.raises(ValueError, match="out of range"):
            cut_dendrogram(Z, n_clusters=0)
        with pytest.raises(ValueError, match="out of range"):
            cut_dendrogram(Z, n_clusters=5)

    def test_malformed_linkage_raises(self):
        with pytest.raises(ValueError, match="must be a"):
            cut_dendrogram(np.zeros((3, 3)), n_clusters=2)
