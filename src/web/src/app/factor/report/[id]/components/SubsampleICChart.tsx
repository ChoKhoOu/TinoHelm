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
  CHART_COLORS,
  CHART_GRID_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";
import type { SubsampleICEntry } from "./types";

interface SubsampleICChartProps {
  data: SubsampleICEntry[];
  height?: number;
}

/**
 * Subsample IC bar chart — one bar per time segment (month / quarter).
 *
 * Charts Spec: semantic green/red by sign.  Compact X axis — period labels
 * rotate when they would overlap.  Uses the data-item ``fill`` field
 * pattern (per R-new Recharts guidance) instead of ``<Cell>`` children.
 */
export function SubsampleICChart({ data, height = 220 }: SubsampleICChartProps) {
  if (!data || data.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        暂无分段 IC 数据
      </div>
    );
  }

  const rows = data.map((d) => ({
    period: d.period,
    ic: d.ic,
    fill: d.ic >= 0 ? CHART_COLORS.success : CHART_COLORS.danger,
  }));

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
          <CartesianGrid {...CHART_GRID_STYLE} />
          <XAxis
            dataKey="period"
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            minTickGap={20}
          />
          <YAxis
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            width={44}
            tickFormatter={(v: number) => v.toFixed(2)}
          />
          <RechartsTooltip
            {...CHART_TOOLTIP_PROPS}
            formatter={(v: unknown) => [
              typeof v === "number" ? v.toFixed(4) : String(v ?? ""),
              "IC",
            ]}
          />
          <ReferenceLine y={0} stroke="var(--t3)" strokeDasharray="3 3" />
          <Bar
            dataKey="ic"
            radius={[2, 2, 0, 0]}
            animationDuration={CHART_ANIMATION.duration}
            animationEasing={CHART_ANIMATION.easing}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
