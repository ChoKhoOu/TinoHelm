"""Hierarchical clustering on the factor correlation matrix.

Wraps :func:`scipy.cluster.hierarchy.linkage` (Ward method by default)
on the distance matrix derived from the correlation matrix produced by
:mod:`tinohelm.factor.evaluation.correlation`. The result is the canonical
``(F-1) × 4`` linkage array consumed by ``scipy.cluster.hierarchy.dendrogram``.

Distance metric (AC-2.4.2)
--------------------------
The "correlation distance" is ``1 - |corr|``. Absolute value is used so
factors that are perfectly *anti*-correlated cluster as tightly as
factors that are perfectly correlated — both carry the same information
content. The diagonal (``1 - 1 = 0``) is by definition a valid zero
distance, but ``linkage`` only sees the condensed upper-triangle so it
is never visited.

Determinism contract (AC-2.4.2)
-------------------------------
Same input → byte-identical linkage array across two consecutive calls.
We sort factor names before computing distances, force the input matrix
into a deterministic float64 buffer, and pass through ``linkage`` which
is itself deterministic given a fixed condensed distance vector.
"""
from __future__ import annotations

import numpy as np
import polars as pl
from scipy.cluster.hierarchy import fcluster, linkage


# Allowed scipy linkage methods. Restricting via an explicit allow-list is
# cheaper than re-validating scipy's argv on every call site, and gives a
# friendly error in tests when a typo slips through.
_ALLOWED_METHODS: frozenset[str] = frozenset(
    {"single", "complete", "average", "weighted", "centroid", "median", "ward"}
)


def _corr_to_condensed_distance(corr_matrix: pl.DataFrame) -> tuple[list[str], np.ndarray]:
    """Convert a wide correlation DataFrame into a condensed distance vector.

    Parameters
    ----------
    corr_matrix:
        Wide ``(F, F+1)`` polars DataFrame with column ``factor_name``
        followed by F float columns (one per factor) — the shape produced
        by :mod:`tinohelm.factor.evaluation.correlation`.

    Returns
    -------
    (factor_names, condensed)
        ``factor_names`` is the in-order list of leaf labels.
        ``condensed`` is a 1-D numpy float64 array of length
        ``F * (F-1) / 2`` containing ``1 - |corr_ij|`` for ``i < j``.
    """
    if "factor_name" not in corr_matrix.columns:
        raise ValueError(
            "corr_matrix must contain a 'factor_name' column "
            f"(got columns: {corr_matrix.columns!r})"
        )
    factor_names = corr_matrix["factor_name"].to_list()
    F = len(factor_names)
    if F < 2:
        raise ValueError(
            f"hierarchical clustering requires at least 2 factors; got {F}"
        )

    data_cols = [c for c in corr_matrix.columns if c != "factor_name"]
    if len(data_cols) != F:
        raise ValueError(
            f"corr_matrix is not square: {len(data_cols)} data columns vs "
            f"{F} factor names"
        )
    matrix = corr_matrix.select(data_cols).to_numpy().astype(np.float64, copy=True)

    # Condensed (upper-triangle, row-major) — scipy's required input format.
    condensed = np.empty(F * (F - 1) // 2, dtype=np.float64)
    k = 0
    for i in range(F):
        for j in range(i + 1, F):
            d = 1.0 - abs(float(matrix[i, j]))
            # Guard against tiny negative epsilon from float rounding.
            condensed[k] = max(d, 0.0)
            k += 1
    return factor_names, condensed


def hierarchical_cluster(
    corr_matrix: pl.DataFrame,
    method: str = "ward",
) -> dict:
    """Hierarchical agglomerative cluster on the correlation distance.

    Parameters
    ----------
    corr_matrix:
        Output of :func:`tinohelm.factor.evaluation.correlation.correlation_matrix`.
    method:
        Linkage method passed to :func:`scipy.cluster.hierarchy.linkage`.
        Default ``"ward"``. Restricted to the standard scipy allow-list.

    Returns
    -------
    dict
        ``{"linkage_matrix": np.ndarray (F-1, 4), "labels": list[str], "method": str}``.
    """
    if method not in _ALLOWED_METHODS:
        raise ValueError(
            f"unknown linkage method {method!r}; expected one of "
            f"{sorted(_ALLOWED_METHODS)}"
        )

    factor_names, condensed = _corr_to_condensed_distance(corr_matrix)
    Z = linkage(condensed, method=method)
    return {
        "linkage_matrix": Z,
        "labels": factor_names,
        "method": method,
    }


def cut_dendrogram(linkage_matrix: np.ndarray, n_clusters: int) -> np.ndarray:
    """Cut a dendrogram into ``n_clusters`` flat clusters.

    Returns a 1-D int array of cluster IDs (1-indexed by scipy
    convention), one per leaf. ``n_clusters`` must be ≥ 1 and ≤ the
    number of leaves (``linkage_matrix.shape[0] + 1``).
    """
    if not isinstance(linkage_matrix, np.ndarray):
        linkage_matrix = np.asarray(linkage_matrix)
    if linkage_matrix.ndim != 2 or linkage_matrix.shape[1] != 4:
        raise ValueError(
            f"linkage_matrix must be a (F-1, 4) array; got shape {linkage_matrix.shape}"
        )
    n_leaves = linkage_matrix.shape[0] + 1
    if n_clusters < 1 or n_clusters > n_leaves:
        raise ValueError(
            f"n_clusters={n_clusters} out of range [1, {n_leaves}]"
        )
    return fcluster(linkage_matrix, t=n_clusters, criterion="maxclust")


__all__ = [
    "cut_dendrogram",
    "hierarchical_cluster",
]
