"use client";

import { useMemo } from "react";
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
import { HelpTip, SectionLabel } from "@/components/qds";
import type { RollingICSmallMultiples as RollingICData } from "../types";

interface RollingICSmallMultiplesProps {
  rolling: RollingICData;
}

/**
 * Per-factor rolling-IC mini chart grid (small multiples pattern).
 *
 * Backend produces a 30-bar rolling mean of each factor's IC time series.
 * Each chart shares its own y-axis so within-factor variance is visible; we
 * intentionally do NOT force a shared scale (Cleveland small-multiples
 * principle — same scale only when comparing absolute deltas).
 *
 * Charts Spec: ``--acc`` line, 1.5px stroke, dashed ``--t2`` zero-reference,
 * compact 140px tile height (1/3 of full IC chart).
 */
export function RollingICSmallMultiples({ rolling }: RollingICSmallMultiplesProps) {
  const { factors, rolling_ic_window, series } = rolling;

  if (!factors || factors.length === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground font-mono">
        暂无 rolling IC 数据
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <SectionLabel>Rolling IC · {rolling_ic_window} 期</SectionLabel>
        <div className="ml-auto flex items-center gap-1">
          <HelpTip
            text={`每个子图为单因子 IC 的 ${rolling_ic_window}-bar rolling mean。短序列 (< ${rolling_ic_window}) 直接显示原始 IC。`}
          />
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {factors.map((factor) => (
          <SmallMultiple
            key={factor}
            factor={factor}
            values={series[factor] ?? []}
          />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SmallMultiple — single factor mini chart                           */
/* ------------------------------------------------------------------ */

interface SmallMultipleProps {
  factor: string;
  values: (number | null)[];
}

function SmallMultiple({ factor, values }: SmallMultipleProps) {
  const data = useMemo(
    () =>
      values.map((v, idx) => ({
        idx,
        ic: v,
      })),
    [values],
  );

  const summary = useMemo(() => {
    const valid = values.filter((v): v is number => v != null);
    if (valid.length === 0) return { mean: null, last: null };
    const mean = valid.reduce((a, b) => a + b, 0) / valid.length;
    const last = valid[valid.length - 1];
    return { mean, last };
  }, [values]);

  return (
    <div className="rounded-md border bg-input p-3 flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[0.78rem] font-medium text-foreground truncate">
          {factor}
        </span>
        <span
          className={`font-mono text-[0.7rem] ${
            summary.last == null
              ? "text-muted-foreground"
              : summary.last >= 0
              ? "text-qds-success"
              : "text-destructive"
          }`}
        >
          {summary.last == null ? "—" : summary.last.toFixed(4)}
        </span>
      </div>
      <div className="font-mono text-[0.6rem] text-muted-foreground">
        mean {summary.mean == null ? "—" : summary.mean.toFixed(4)} · n {values.length}
      </div>
      <div className="h-[120px]">
        {data.length === 0 ? (
          <div className="h-full flex items-center justify-center text-[0.65rem] text-muted-foreground font-mono">
            empty
          </div>
        ) : (
          <ResponsiveContainer>
            <LineChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid {...CHART_GRID_STYLE} />
              <XAxis
                dataKey="idx"
                tick={CHART_AXIS_STYLE}
                tickLine={false}
                axisLine={false}
                interval="preserveStartEnd"
                minTickGap={24}
                hide
              />
              <YAxis
                tick={CHART_AXIS_STYLE}
                tickLine={false}
                axisLine={false}
                width={36}
                tickFormatter={(v: number) => v.toFixed(2)}
              />
              <RechartsTooltip
                {...CHART_TOOLTIP_PROPS}
                formatter={(v: unknown) => [
                  typeof v === "number" ? v.toFixed(4) : String(v ?? ""),
                  "IC",
                ]}
                labelFormatter={(label: unknown) => `idx ${String(label ?? "")}`}
              />
              <ReferenceLine
                y={0}
                stroke="var(--t2)"
                strokeDasharray="3 3"
                strokeOpacity={0.5}
                label={{ ...CHART_LABEL_STYLE, value: "0", position: "right" }}
              />
              <Line
                type="monotone"
                dataKey="ic"
                stroke="var(--acc)"
                strokeWidth={1.5}
                dot={false}
                connectNulls={false}
                animationDuration={CHART_ANIMATION.duration}
                animationEasing={CHART_ANIMATION.easing}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
