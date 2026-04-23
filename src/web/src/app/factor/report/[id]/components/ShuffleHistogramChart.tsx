"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_GRID_STYLE,
  CHART_LABEL_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";

interface ShuffleHistogramChartProps {
  /** Null histogram from shuffle test (N permutations). */
  distribution: { bin_start: number; bin_end: number; count: number }[];
  /** Real IC — rendered as a vertical ReferenceLine. */
  realIc: number;
  height?: number;
}

/**
 * Shuffle test histogram — null IC distribution from N random permutations
 * with the observed IC overlaid as a red reference line.
 *
 * Charts Spec: neutral ``--t3`` bars (secondary-series style), real-IC line
 * in ``--dan``.  Uses bin centers on the X axis (never bin edges).
 */
export function ShuffleHistogramChart({
  distribution,
  realIc,
  height = 220,
}: ShuffleHistogramChartProps) {
  if (!distribution || distribution.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        暂无 shuffle 分布数据
      </div>
    );
  }

  const rows = distribution.map((b) => ({
    center: (b.bin_start + b.bin_end) / 2,
    count: b.count,
  }));

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 16, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...CHART_GRID_STYLE} />
          <XAxis
            dataKey="center"
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => v.toFixed(2)}
            minTickGap={24}
          />
          <YAxis
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            width={40}
          />
          <RechartsTooltip
            {...CHART_TOOLTIP_PROPS}
            formatter={(v: unknown) => [`${v ?? ""}`, "频次"]}
            labelFormatter={(c: unknown) =>
              typeof c === "number" ? `IC 中心 ${c.toFixed(3)}` : String(c ?? "")
            }
          />
          <ReferenceLine
            x={realIc}
            stroke="var(--dan)"
            strokeWidth={2}
            label={{
              ...CHART_LABEL_STYLE,
              fill: "var(--dan)",
              value: `Real IC = ${realIc.toFixed(3)}`,
              position: "top",
            }}
          />
          <Bar
            dataKey="count"
            fill="var(--t3)"
            fillOpacity={0.85}
            radius={[2, 2, 0, 0]}
            animationDuration={CHART_ANIMATION.duration}
            animationEasing={CHART_ANIMATION.easing}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
