"use client";

import { InlineError } from "@/components/qds";
import type { EquityCurvePoint } from "../types";

/* ------------------------------------------------------------------ */
/*  Constants & helpers                                                */
/* ------------------------------------------------------------------ */

/** Static estimate: 200 sampled points × avg segment ~5px ≈ 1000; safety margin ×3 */
const PATH_LENGTH = 3000;

/** Map data values to SVG viewport coordinates. */
function buildPath(
  points: { x: number; y: number }[],
): string {
  if (points.length === 0) return "";
  return points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)},${p.y.toFixed(2)}`)
    .join(" ");
}

function buildAreaPath(
  points: { x: number; y: number }[],
  viewH: number,
): string {
  if (points.length === 0) return "";
  const stroke = buildPath(points);
  const lastX = points[points.length - 1].x.toFixed(2);
  const firstX = points[0].x.toFixed(2);
  return `${stroke} L${lastX},${viewH} L${firstX},${viewH} Z`;
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

interface OverviewEquitySvgProps {
  data: EquityCurvePoint[];
  height?: number;
}

export function OverviewEquitySvg({ data, height = 280 }: OverviewEquitySvgProps) {
  /* FR-092 — empty data fallback */
  if (!data || data.length === 0) {
    return (
      <div className="flex items-center justify-center" style={{ height }}>
        <InlineError variant="hint">暂无权益曲线数据</InlineError>
      </div>
    );
  }

  /* Sample to ≤200 points for performance */
  const maxPoints = 200;
  const step = data.length > maxPoints ? Math.ceil(data.length / maxPoints) : 1;
  const sampled = data.filter((_, i) => i % step === 0);

  const VIEW_W = 1000;
  const VIEW_H = height;

  /* ── Equity scale ──────────────────────────────────────────────── */
  const equityValues = sampled.map((p) => p.equity);
  const minEq = Math.min(...equityValues);
  const maxEq = Math.max(...equityValues);
  const eqRange = maxEq - minEq || 1;

  /* Top 15% / bottom 10% padding so strokes don't clip at edges */
  const EQ_TOP_PAD = VIEW_H * 0.10;
  const EQ_BOT_PAD = VIEW_H * 0.10;
  const eqUsable = VIEW_H - EQ_TOP_PAD - EQ_BOT_PAD;

  const eqPoints = sampled.map((p, i) => ({
    x: (i / (sampled.length - 1)) * VIEW_W,
    y: EQ_TOP_PAD + (1 - (p.equity - minEq) / eqRange) * eqUsable,
  }));

  /* ── Drawdown scale ─────────────────────────────────────────────── */
  const ddValues = sampled.map((p) => p.drawdown_pct ?? 0);
  const minDd = Math.min(...ddValues); /* most negative */
  const maxDd = Math.max(...ddValues); /* closest to 0  */
  const ddRange = maxDd - minDd || 1;

  /* Drawdown occupies lower 35% of viewBox (overlay on equity area) */
  const DD_TOP = VIEW_H * 0.62;
  const DD_BOT_PAD = VIEW_H * 0.05;
  const ddUsable = VIEW_H - DD_TOP - DD_BOT_PAD;

  const ddPoints = sampled.map((p, i) => ({
    x: (i / (sampled.length - 1)) * VIEW_W,
    /* minDd is the worst (most negative) → maps to DD_TOP + ddUsable */
    y: DD_TOP + (1 - (p.drawdown_pct ?? 0 - minDd) / ddRange) * ddUsable,
  }));

  /* ── Path strings ───────────────────────────────────────────────── */
  const eqLinePath = buildPath(eqPoints);
  const eqAreaPath = buildAreaPath(eqPoints, VIEW_H);
  const ddLinePath = buildPath(ddPoints);
  const ddAreaPath = buildAreaPath(ddPoints, VIEW_H);

  /* ── Dash animation style (inline — required by AC-I-3) ─────────── */
  const dashStyle: React.CSSProperties = {
    strokeDasharray: PATH_LENGTH,
    strokeDashoffset: PATH_LENGTH,
    animation: `dash 1.8s 0.1s var(--eo, ease-out) forwards`,
  };

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      className="w-full h-auto motion-reduce:[&_path]:[animation-duration:0s]"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {/* ── Gradient defs ───────────────────────────────────────────── */}
      <defs>
        <linearGradient id="ovEqGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="5%"  stopColor="var(--info)" stopOpacity="0.30" />
          <stop offset="95%" stopColor="var(--info)" stopOpacity="0.02" />
        </linearGradient>
        <linearGradient id="ovDdGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="5%"  stopColor="var(--dan)" stopOpacity="0.22" />
          <stop offset="95%" stopColor="var(--dan)" stopOpacity="0.02" />
        </linearGradient>
      </defs>

      {/* ── Horizontal grid lines (3 lines) ─────────────────────────── */}
      {[0.25, 0.5, 0.75].map((ratio) => (
        <line
          key={ratio}
          x1={0}
          y1={VIEW_H * ratio}
          x2={VIEW_W}
          y2={VIEW_H * ratio}
          stroke="var(--bd)"
          strokeWidth="0.5"
          strokeDasharray="4 4"
        />
      ))}

      {/* ── Equity area fill ─────────────────────────────────────────── */}
      <path
        d={eqAreaPath}
        fill="url(#ovEqGrad)"
        stroke="none"
      />

      {/* ── Drawdown area fill ───────────────────────────────────────── */}
      <path
        d={ddAreaPath}
        fill="url(#ovDdGrad)"
        stroke="none"
      />

      {/* ── Equity stroke (animated) ─────────────────────────────────── */}
      <path
        d={eqLinePath}
        fill="none"
        stroke="var(--info)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={dashStyle}
      />

      {/* ── Drawdown stroke (animated, delayed) ──────────────────────── */}
      <path
        d={ddLinePath}
        fill="none"
        stroke="var(--dan)"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{
          ...dashStyle,
          animationDelay: "200ms",
        }}
      />
    </svg>
  );
}
