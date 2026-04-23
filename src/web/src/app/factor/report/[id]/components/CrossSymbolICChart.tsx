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
import type { CrossSymbolICEntry } from "./types";

interface CrossSymbolICChartProps {
  data: CrossSymbolICEntry[];
  height?: number;
}

/**
 * Cross-symbol IC horizontal bar chart — one bar per symbol.
 *
 * Vertical layout keeps symbol names readable on the Y axis.  Uses data-item
 * ``fill`` field pattern (per R-new Recharts guidance) rather than
 * ``<Cell>`` children.
 */
export function CrossSymbolICChart({
  data,
  height = 220,
}: CrossSymbolICChartProps) {
  if (!data || data.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        暂无跨品种 IC 数据
      </div>
    );
  }

  const rows = data.map((d) => ({
    symbol: d.symbol,
    ic: d.ic,
    n_obs: d.n_obs,
    fill: d.ic >= 0 ? CHART_COLORS.success : CHART_COLORS.danger,
  }));

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
        >
          <CartesianGrid {...CHART_GRID_STYLE} />
          <XAxis
            type="number"
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v: number) => v.toFixed(2)}
          />
          <YAxis
            type="category"
            dataKey="symbol"
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            width={100}
          />
          <RechartsTooltip
            {...CHART_TOOLTIP_PROPS}
            formatter={(v: unknown, _name: unknown, item: unknown) => {
              const payload = (
                item as { payload?: { n_obs?: number } } | undefined
              )?.payload;
              const obs = payload?.n_obs;
              const icStr =
                typeof v === "number" ? v.toFixed(4) : String(v ?? "");
              return [obs != null ? `${icStr} (n=${obs})` : icStr, "IC"];
            }}
          />
          <ReferenceLine x={0} stroke="var(--t3)" strokeDasharray="3 3" />
          <Bar
            dataKey="ic"
            radius={[0, 3, 3, 0]}
            animationDuration={CHART_ANIMATION.duration}
            animationEasing={CHART_ANIMATION.easing}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
