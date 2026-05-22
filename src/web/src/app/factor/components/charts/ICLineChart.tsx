"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
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
import { SERIES_COLORS } from "../types";

interface ICLineChartProps {
  data: { date: string; ic: number }[];
  height?: number;
}

/**
 * IC time-series line chart.
 *
 * Charts Spec: single primary series on ``--acc``, horizontal grid only,
 * dashed ``--t2`` zero-reference line with ``"IC = 0"`` label.
 */
export function ICLineChart({ data, height = 220 }: ICLineChartProps) {
  if (!data || data.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        暂无 IC 时序
      </div>
    );
  }

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...CHART_GRID_STYLE} />
          <XAxis
            dataKey="date"
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            minTickGap={32}
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
          <ReferenceLine
            y={0}
            stroke="var(--t2)"
            strokeDasharray="3 3"
            strokeOpacity={0.5}
            label={{ ...CHART_LABEL_STYLE, value: "IC = 0", position: "right" }}
          />
          <Line
            type="monotone"
            dataKey="ic"
            stroke={SERIES_COLORS.ic}
            strokeWidth={1.5}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0, fill: SERIES_COLORS.ic }}
            animationDuration={CHART_ANIMATION.duration}
            animationEasing={CHART_ANIMATION.easing}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
