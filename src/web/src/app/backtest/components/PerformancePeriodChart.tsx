"use client";

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHART_GRID_STYLE, CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import type { AnnualReturn } from "../types";
import { ChartCard, ChartPlaceholder, clamp, downsample } from "./PerformanceHelpers";

export function PerformanceMonthlyHeatmap({
  monthlyReturns,
}: {
  monthlyReturns?: Array<{ period: string; return_pct: number }>;
}) {
  const { years, grid, maxAbs } = useMemo(() => {
    if (!monthlyReturns?.length) {
      return {
        years: [] as number[],
        grid: new Map<string, number>(),
        maxAbs: 1,
      };
    }
    const grid = new Map<string, number>();
    const yearSet = new Set<number>();

    for (const m of monthlyReturns) {
      const [y, mo] = m.period.split("-").map(Number);
      if (!y || !mo) continue;
      yearSet.add(y);
      grid.set(`${y}-${mo}`, m.return_pct);
    }

    const years = Array.from(yearSet).sort();
    const maxAbs = Math.max(1, ...Array.from(grid.values()).map(Math.abs));

    return { years, grid, maxAbs };
  }, [monthlyReturns]);

  const monthLabels = [
    "1月",
    "2月",
    "3月",
    "4月",
    "5月",
    "6月",
    "7月",
    "8月",
    "9月",
    "10月",
    "11月",
    "12月",
  ];

  if (!years.length) {
    return (
      <ChartCard title="月度收益热力图">
        <ChartPlaceholder />
      </ChartCard>
    );
  }

  function cellColor(val: number | undefined): string {
    if (val == null) return "rgba(255,255,255,0.03)";
    const intensity = clamp(Math.abs(val) / maxAbs, 0, 1);
    if (val > 0) {
      const g = Math.round(80 + intensity * 137);
      return `rgba(38, ${g}, 127, ${0.15 + intensity * 0.55})`;
    } else {
      const r = Math.round(150 + intensity * 89);
      return `rgba(${r}, 83, 80, ${0.15 + intensity * 0.55})`;
    }
  }

  // Build per-year row data for Recharts BarChart
  const rowData = years.map((yr) =>
    monthLabels.map((label, i) => ({
      month: label,
      value: grid.get(`${yr}-${i + 1}`) as number | undefined,
      display: 1,
    }))
  );

  return (
    <ChartCard title="月度收益热力图">
      <div className="overflow-x-auto" style={{ minWidth: 480 }}>
        {/* Month header labels */}
        <div className="flex items-center" style={{ paddingLeft: 40 }}>
          {monthLabels.map((m) => (
            <div
              key={m}
              className="flex-1 text-center font-mono text-[9px] text-muted-foreground"
            >
              {m}
            </div>
          ))}
        </div>
        {/* Year rows — one BarChart per year */}
        {years.map((yr, yi) => (
          <div key={yr} className="flex items-center">
            <div className="shrink-0 font-mono text-[9px] text-muted-foreground w-10">
              {yr}
            </div>
            <div className="flex-1" style={{ height: 30 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart
                  data={rowData[yi]}
                  margin={{ top: 1, right: 1, left: 1, bottom: 1 }}
                  barCategoryGap={2}
                >
                  <XAxis hide dataKey="month" />
                  <RechartsTooltip
                    {...CHART_TOOLTIP_PROPS}
                    cursor={false}
                    formatter={(
                      _: unknown,
                      __: unknown,
                      props: { payload?: { value?: number; month?: string } }
                    ) => {
                      const v = props.payload?.value;
                      if (v == null) return ["—", ""];
                      return [
                        `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`,
                        `${yr}年${props.payload?.month}`,
                      ];
                    }}
                  />
                  <Bar dataKey="display" radius={3} isAnimationActive={false}>
                    {rowData[yi].map((d, i) => (
                      <Cell key={i} fill={cellColor(d.value)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        ))}
      </div>
    </ChartCard>
  );
}

export function PerformanceAnnualReturnsChart({
  annualReturns,
}: {
  annualReturns?: AnnualReturn[];
}) {
  if (!annualReturns?.length) {
    return (
      <ChartCard title="年度收益">
        <ChartPlaceholder />
      </ChartCard>
    );
  }

  return (
    <ChartCard title="年度收益">
      <ResponsiveContainer width="100%" height={180}>
        <BarChart
          data={annualReturns}
          margin={{ top: 8, right: 8, left: 8, bottom: 4 }}
        >
          <CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
          <XAxis
            dataKey="year"
            tick={{ fill: "var(--t2)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={{ fill: "var(--t2)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            width={42}
          />
          <RechartsTooltip
            {...CHART_TOOLTIP_PROPS}
            cursor={{ fill: "var(--bd)" }}
            formatter={(value: unknown) => {
              const v = Number(value);
              return [
                <span
                  key="v"
                  style={{ color: v >= 0 ? "var(--suc)" : "var(--dan)" }}
                >
                  {v >= 0 ? "+" : ""}
                  {v.toFixed(2)}%
                </span>,
                "年度收益",
              ];
            }}
          />
          <ReferenceLine
            y={0}
            stroke="rgba(255,255,255,0.15)"
            strokeWidth={1}
          />
          <Bar dataKey="return_pct" radius={[3, 3, 0, 0]}>
            {annualReturns.map((entry, index) => (
              <Cell
                key={`ar-${index}`}
                fill={entry.return_pct >= 0 ? "var(--suc)" : "var(--dan)"}
                fillOpacity={0.8}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function PerformanceWeeklyReturnsChart({
  weeklyReturns,
}: {
  weeklyReturns?: Array<{ period: string; return_pct: number }>;
}) {
  const data = useMemo(() => {
    if (!weeklyReturns?.length) return [];
    return downsample(weeklyReturns, 120).map((w) => ({
      t: w.period.slice(5),
      return_pct: w.return_pct,
    }));
  }, [weeklyReturns]);

  if (!data.length) {
    return (
      <ChartCard title="周度收益">
        <ChartPlaceholder />
      </ChartCard>
    );
  }

  return (
    <ChartCard title="周度收益">
      <ResponsiveContainer width="100%" height={160}>
        <BarChart
          data={data}
          margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
        >
          <CartesianGrid {...CHART_GRID_STYLE} strokeDasharray="3 3" />
          <XAxis
            dataKey="t"
            tick={{ fill: "var(--t2)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "var(--t2)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            width={38}
          />
          <RechartsTooltip
            {...CHART_TOOLTIP_PROPS}
            cursor={{ fill: "var(--bd)" }}
            formatter={(value: unknown) => {
              const v = Number(value);
              return [
                <span
                  key="v"
                  style={{ color: v >= 0 ? "var(--suc)" : "var(--dan)" }}
                >
                  {v >= 0 ? "+" : ""}
                  {v.toFixed(2)}%
                </span>,
                "周收益",
              ];
            }}
          />
          <ReferenceLine
            y={0}
            stroke="rgba(255,255,255,0.15)"
            strokeWidth={1}
          />
          <Bar dataKey="return_pct" radius={[2, 2, 0, 0]}>
            {data.map((entry, index) => (
              <Cell
                key={`wr-${index}`}
                fill={entry.return_pct >= 0 ? "var(--suc)" : "var(--dan)"}
                fillOpacity={0.75}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
