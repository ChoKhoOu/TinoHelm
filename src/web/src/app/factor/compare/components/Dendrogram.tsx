"use client";

import { useMemo } from "react";
import { HelpTip, SectionLabel } from "@/components/qds";
import type { Dendrogram as DendrogramData } from "../types";

interface DendrogramProps {
  data: DendrogramData;
}

/* ------------------------------------------------------------------ */
/*  scipy linkage decoder                                              */
/* ------------------------------------------------------------------ */

interface NodeLayout {
  /** x-position in [0, 1] (will be multiplied by chart width). */
  x: number;
  /** Cluster height (linkage distance).  Leaves have height 0. */
  height: number;
  /** Indices of constituent leaves (left → right). */
  leaves: number[];
}

/**
 * Decode a scipy linkage matrix into per-cluster {x, height, leaves}.
 *
 * scipy convention: row ``k`` of an (n-1) × 4 matrix describes the merge
 * step ``k``.  Children indices < n are leaves; ≥ n are previous clusters
 * (offset by ``n``).  The result builds a top-down dendrogram where every
 * cluster has an x-position equal to the centroid of its leaves' positions.
 *
 * Input ``linkage_matrix`` is the JSON-serialised numpy array from
 * ``scipy.cluster.hierarchy.linkage(..., method="ward")``.  Empty matrix
 * (e.g. < 2 valid factors) returns a degenerate layout — caller renders
 * "no data" placeholder.
 */
function buildLayout(
  linkage: number[][],
  labelCount: number,
): {
  leafOrder: number[];
  nodes: Map<number, NodeLayout>;
  maxHeight: number;
} {
  const nodes = new Map<number, NodeLayout>();
  // Initialise leaves at evenly-spaced x positions.
  const n = labelCount;
  for (let i = 0; i < n; i++) {
    nodes.set(i, {
      x: n === 1 ? 0.5 : i / (n - 1),
      height: 0,
      leaves: [i],
    });
  }

  let maxHeight = 0;
  // Process each merge in row order (k = 0 → n-2).
  linkage.forEach((row, k) => {
    const childA = Math.round(row[0]);
    const childB = Math.round(row[1]);
    const dist = Number(row[2]) || 0;
    maxHeight = Math.max(maxHeight, dist);

    const a = nodes.get(childA);
    const b = nodes.get(childB);
    if (!a || !b) return; // defensive: malformed matrix

    const merged: NodeLayout = {
      x: (a.x + b.x) / 2,
      height: dist,
      leaves: [...a.leaves, ...b.leaves],
    };
    nodes.set(n + k, merged);
  });

  // Determine leaf x-order from the final cluster (root) if available; else
  // fall back to identity 0..n-1.
  const root = linkage.length > 0 ? nodes.get(n + linkage.length - 1) : null;
  const leafOrder = root ? root.leaves : Array.from({ length: n }, (_, i) => i);

  // Reposition leaves so they are evenly spaced in encounter order — this
  // is what users expect from a dendrogram: leaves never overlap and follow
  // the cluster traversal.
  leafOrder.forEach((leafIdx, posIdx) => {
    const node = nodes.get(leafIdx);
    if (!node) return;
    node.x = n === 1 ? 0.5 : posIdx / (n - 1);
  });

  // Recompute internal cluster x as centroid of children leaves.
  linkage.forEach((row, k) => {
    const cluster = nodes.get(n + k);
    if (!cluster) return;
    const xs = cluster.leaves
      .map((l) => nodes.get(l)?.x ?? 0)
      .filter((x) => Number.isFinite(x));
    if (xs.length > 0) {
      cluster.x = xs.reduce((s, x) => s + x, 0) / xs.length;
    }
  });

  return { leafOrder, nodes, maxHeight };
}

/* ------------------------------------------------------------------ */
/*  Dendrogram                                                          */
/* ------------------------------------------------------------------ */

export function Dendrogram({ data }: DendrogramProps) {
  const { linkage_matrix: linkage, labels } = data;

  const layout = useMemo(
    () => buildLayout(linkage, labels.length),
    [linkage, labels.length],
  );

  if (!labels || labels.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground font-mono">
        暂无聚类数据
      </div>
    );
  }

  if (linkage.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-6">
        <div className="flex items-center gap-2 mb-3">
          <SectionLabel>Dendrogram</SectionLabel>
        </div>
        <div className="text-center text-sm text-muted-foreground font-mono py-12">
          需至少 2 个完成的因子才能聚类
        </div>
      </div>
    );
  }

  /* SVG drawing parameters. */
  const width = 720;
  const height = 320;
  const paddingX = 40;
  const paddingTopBottom = 24;
  const labelHeight = 60;
  const drawHeight = height - paddingTopBottom - labelHeight;
  const drawWidth = width - paddingX * 2;
  const heightDenom = layout.maxHeight > 0 ? layout.maxHeight : 1;

  /** Convert a cluster height (distance) to SVG y position. Larger height → */
  /** higher up in the chart (closer to top, smaller y).  Linear scale.       */
  const yFromHeight = (h: number): number => {
    const t = h / heightDenom;
    return paddingTopBottom + (1 - t) * drawHeight;
  };

  const xFromUnit = (xUnit: number): number => paddingX + xUnit * drawWidth;

  /* Build edge primitives. */
  const n = labels.length;
  const edges: Array<{ d: string; key: string }> = [];
  linkage.forEach((row, k) => {
    const childAIdx = Math.round(row[0]);
    const childBIdx = Math.round(row[1]);
    const dist = Number(row[2]) || 0;

    const a = layout.nodes.get(childAIdx);
    const b = layout.nodes.get(childBIdx);
    if (!a || !b) return;

    const aX = xFromUnit(a.x);
    const bX = xFromUnit(b.x);
    const aY = yFromHeight(a.height);
    const bY = yFromHeight(b.height);
    const topY = yFromHeight(dist);

    /* Up → across → down edge.  scipy-style "□"-shape. */
    edges.push({
      key: `e-${k}-a`,
      d: `M ${aX} ${aY} L ${aX} ${topY}`,
    });
    edges.push({
      key: `e-${k}-b`,
      d: `M ${bX} ${bY} L ${bX} ${topY}`,
    });
    edges.push({
      key: `e-${k}-h`,
      d: `M ${aX} ${topY} L ${bX} ${topY}`,
    });
  });

  /* Compute leaf labels in display order. */
  const labelEntries = layout.leafOrder.map((leafIdx, posIdx) => {
    const node = layout.nodes.get(leafIdx);
    if (!node) return null;
    return {
      idx: leafIdx,
      pos: posIdx,
      x: xFromUnit(node.x),
      label: labels[leafIdx] ?? `leaf-${leafIdx}`,
    };
  });

  const baselineY = yFromHeight(0);

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <SectionLabel>Dendrogram · 因子聚类</SectionLabel>
        <HelpTip text="基于 IC 时间序列相关性距离 (Ward 法) 的层次聚类。垂直高度 = 聚类距离，距离越小越相似。叶子顺序按聚类遍历重新排列。" />
        <span className="ml-auto font-mono text-[0.62rem] text-muted-foreground">
          {n} 因子 · max d = {layout.maxHeight.toFixed(3)}
        </span>
      </div>
      <div className="overflow-x-auto">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Hierarchical cluster dendrogram"
          className="block"
        >
          {/* Bottom baseline (axis) */}
          <line
            x1={paddingX}
            x2={width - paddingX}
            y1={baselineY}
            y2={baselineY}
            stroke="var(--bd)"
            strokeWidth={1}
          />
          {/* Y-axis ticks (3 levels) */}
          {[0, 0.5, 1].map((t) => {
            const y = paddingTopBottom + (1 - t) * drawHeight;
            return (
              <g key={`tick-${t}`}>
                <line
                  x1={paddingX}
                  x2={width - paddingX}
                  y1={y}
                  y2={y}
                  stroke="var(--chart-grid)"
                  strokeDasharray="2 4"
                  strokeOpacity={0.6}
                />
                <text
                  x={paddingX - 6}
                  y={y + 3}
                  textAnchor="end"
                  fontSize={9}
                  fill="var(--chart-tick)"
                >
                  {(t * heightDenom).toFixed(2)}
                </text>
              </g>
            );
          })}

          {/* Linkage edges — accent orange */}
          {edges.map((e) => (
            <path
              key={e.key}
              d={e.d}
              stroke="var(--acc)"
              strokeWidth={1.5}
              fill="none"
              strokeLinecap="square"
            />
          ))}

          {/* Leaf nodes — accent orange dot at baseline */}
          {labelEntries.map((entry) =>
            entry == null ? null : (
              <circle
                key={`leaf-${entry.idx}`}
                cx={entry.x}
                cy={baselineY}
                r={3.5}
                fill="var(--acc)"
              />
            ),
          )}

          {/* Leaf labels — rotated for legibility when crowded */}
          {labelEntries.map((entry) =>
            entry == null ? null : (
              <text
                key={`label-${entry.idx}`}
                x={entry.x}
                y={baselineY + 12}
                textAnchor="end"
                transform={`rotate(-35, ${entry.x}, ${baselineY + 12})`}
                fontSize={10}
                fill="var(--t1)"
                style={{ fontFeatureSettings: '"tnum"' }}
              >
                {entry.label}
              </text>
            ),
          )}
        </svg>
      </div>
    </div>
  );
}
