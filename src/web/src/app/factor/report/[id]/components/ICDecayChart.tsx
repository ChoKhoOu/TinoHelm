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

interface ICDecayChartProps {
  data: { lag: number; ic: number }[];
  height?: number;
}

/**
 * IC decay bar chart — one bar per lag.
 *
 * Charts Spec: bars coloured by sign (``--suc`` / ``--dan``) so non-monotonic
 * decays are immediately visible.  Zero-reference line dashed ``--t3``.
 *
 * Uses the data-item ``fill`` field pattern (per R-new Recharts guidance)
 * rather than ``<Cell>`` children, matching ``QuantilePnlChart`` from s18.
 */
export function ICDecayChart({ data, height = 220 }: ICDecayChartProps) {
  if (!data || data.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        暂无 IC 衰减数据
      </div>
    );
  }

  const rows = data.map((d) => ({
    lag: d.lag,
    ic: d.ic,
    fill: d.ic >= 0 ? CHART_COLORS.success : CHART_COLORS.danger,
  }));

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...CHART_GRID_STYLE} />
          <XAxis
            dataKey="lag"
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => `lag ${v}`}
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
            labelFormatter={(lag: unknown) => `lag ${String(lag ?? "")}`}
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
