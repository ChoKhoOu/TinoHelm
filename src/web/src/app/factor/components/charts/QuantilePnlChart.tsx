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
} from "recharts";
import {
  CHART_ANIMATION,
  CHART_AXIS_STYLE,
  CHART_COLORS,
  CHART_GRID_STYLE,
  CHART_TOOLTIP_PROPS,
} from "@/lib/chartTheme";

interface QuantilePnlChartProps {
  /** ``Qk`` → average forward return (decimal, e.g. 0.0032). */
  quantilePnl: Record<string, number>;
  height?: number;
}

/**
 * Quantile average-return bar chart — one bar per bucket.
 *
 * Charts Spec: ``--suc`` for positive, ``--dan`` for negative (always
 * semantic, never decorative).  Bar ordering follows the ``Qk`` ASCII sort so
 * ``Q0..Q4`` appears in ascending factor-value order.
 */
export function QuantilePnlChart({ quantilePnl, height = 220 }: QuantilePnlChartProps) {
  const data = useMemo(
    () =>
      Object.entries(quantilePnl)
        .sort(([a], [b]) => a.localeCompare(b, "en"))
        .map(([label, value]) => ({
          label,
          value,
          /** Forward return is a decimal — surface as % in the UI (∗100). */
          pct: value * 100,
          fill: value >= 0 ? CHART_COLORS.success : CHART_COLORS.danger,
        })),
    [quantilePnl],
  );

  if (data.length === 0) {
    return (
      <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
        暂无分位数据
      </div>
    );
  }

  return (
    <div style={{ width: "100%", height }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid {...CHART_GRID_STYLE} />
          <XAxis
            dataKey="label"
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={CHART_AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            width={44}
            tickFormatter={(v: number) => `${v.toFixed(2)}%`}
          />
          <RechartsTooltip
            {...CHART_TOOLTIP_PROPS}
            formatter={(_v: unknown, _n: unknown, item: unknown) => {
              const payload = (item as { payload?: { pct?: number } } | undefined)
                ?.payload;
              const pct = payload?.pct ?? 0;
              return [`${pct.toFixed(3)}%`, "平均收益"];
            }}
            labelFormatter={(label: unknown) => `分位 ${String(label ?? "")}`}
          />
          <Bar
            dataKey="pct"
            radius={[2, 2, 0, 0]}
            animationDuration={CHART_ANIMATION.duration}
            animationEasing={CHART_ANIMATION.easing}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
