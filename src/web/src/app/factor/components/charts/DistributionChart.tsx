"use client";

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
  ReferenceLine,
} from "recharts";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_COLORS,
  CHART_GRID_STYLE,
  CHART_LABEL_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";

interface DistributionChartProps {
  /** Backend shape: ``{ bin_start, bin_end, count }`` per bucket. */
  histogram: { bin_start: number; bin_end: number; count: number }[];
  /** Distribution statistics — the chart only reads ``mean`` for the dashed line. */
  stats?: Record<string, number>;
  height?: number;
}

/**
 * Factor value distribution histogram.
 *
 * Charts Spec: ``--info`` bars (secondary series semantics), dashed ``--t0``
 * mean reference line.  X-axis shows bin *centers*, never bin edges.
 */
export function DistributionChart({ histogram, stats, height = 220 }: DistributionChartProps) {
  const data = useMemo(
    () =>
      histogram.map((bin) => ({
        center: (bin.bin_start + bin.bin_end) / 2,
        count: bin.count,
      })),
    [histogram],
  );

  if (data.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        暂无分布数据
      </div>
    );
  }

  const mean = stats?.mean;

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
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
            formatter={(v: unknown) => [`${v ?? ""}`, "计数"]}
            labelFormatter={(c: unknown) =>
              typeof c === "number" ? `中心 ${c.toFixed(3)}` : String(c ?? "")
            }
          />
          {mean != null && Number.isFinite(mean) && (
            <ReferenceLine
              x={mean}
              stroke="var(--t0)"
              strokeDasharray="4 3"
              label={{ ...CHART_LABEL_STYLE, value: "μ", position: "top" }}
            />
          )}
          <Bar
            dataKey="count"
            fill={CHART_COLORS.info}
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
