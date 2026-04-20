"use client";

import { useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  AreaChart,
  Area,
  ScatterChart,
  Scatter,
  Cell,
  ReferenceLine,
  Legend,
  ErrorBar,
} from "recharts";
import {
  CHART_TOOLTIP_PROPS,
  CHART_AXIS_STYLE,
  CHART_GRID_STYLE,
  CHART_REFERENCE_LINE,
  CHART_COLORS,
  CHART_LEGEND_STYLE,
} from "@/lib/chartTheme";
import type {
  TradePnlDistributionBin,
  CumulativeTradePnl,
  TradePnlScatterPoint,
  MaeMfePoint,
  HoldingTimeBin,
  StreakEntry,
  LongVsShort,
  ReturnByGroup,
} from "../types";
import {
  ACCENT_GREEN,
  ACCENT_RED,
  ACCENT_LONG,
  ACCENT_SHORT,
  STAT_LABEL_CLS,
  EmptyPlaceholder,
  boxStats,
} from "./TradesHelpers";

/* ------------------------------------------------------------------ */
/*  Trade PnL Distribution Chart                                       */
/* ------------------------------------------------------------------ */

export function PnlDistributionChart({ data }: { data?: TradePnlDistributionBin[] }) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((b) => ({
      label: b.bin_start >= 0
        ? `+${b.bin_start.toFixed(0)}`
        : b.bin_start.toFixed(0),
      count: b.count,
      positive: b.bin_start >= 0,
    }));
  }, [data]);

  if (chartData.length === 0) return <EmptyPlaceholder />;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid {...CHART_GRID_STYLE} />
        <XAxis
          dataKey="label"
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          width={32}
        />
        <RechartsTooltip
          {...CHART_TOOLTIP_PROPS}
          formatter={(value: unknown) => [`${value} 笔`, "交易数"]}
        />
        <Bar dataKey="count" radius={[2, 2, 0, 0]} isAnimationActive animationDuration={1200}>
          {chartData.map((entry, i) => (
            <Cell key={i} fill={entry.positive ? ACCENT_GREEN : ACCENT_RED} fillOpacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ------------------------------------------------------------------ */
/*  Cumulative Trade PnL Chart                                         */
/* ------------------------------------------------------------------ */

export function CumulativePnlChart({ data }: { data?: CumulativeTradePnl[] }) {
  if (!data || data.length === 0) return <EmptyPlaceholder />;

  const finalPnl = data[data.length - 1]?.cumulative_pnl ?? 0;
  const isPositive = finalPnl >= 0;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <defs>
          <linearGradient id="cumPnlFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isPositive ? ACCENT_LONG : ACCENT_RED} stopOpacity={0.25} />
            <stop offset="100%" stopColor={isPositive ? ACCENT_LONG : ACCENT_RED} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid {...CHART_GRID_STYLE} />
        <XAxis
          dataKey="trade_num"
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          label={{ value: "交易序号", position: "insideBottomRight", offset: -4, fontSize: 9, fill: "var(--t3)" }}
        />
        <YAxis
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          width={52}
          tickFormatter={(v) => `${Number(v).toFixed(0)}`}
        />
        <RechartsTooltip
          {...CHART_TOOLTIP_PROPS}
          formatter={(value: unknown) => [`${Number(value).toFixed(2)} USDT`, "累积盈亏"]}
        />
        <ReferenceLine y={0} {...CHART_REFERENCE_LINE} />
        <Area
          type="monotone"
          dataKey="cumulative_pnl"
          stroke={isPositive ? ACCENT_LONG : ACCENT_RED}
          strokeWidth={1.5}
          fill="url(#cumPnlFill)"
          dot={false}
          isAnimationActive
          animationDuration={1500}
          animationEasing="ease-out"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/* ------------------------------------------------------------------ */
/*  Trade PnL Scatter Chart                                            */
/* ------------------------------------------------------------------ */

export function PnlScatterChart({ data }: { data?: TradePnlScatterPoint[] }) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return { long: [], short: [] };
    // Assign trade sequence number, then split by side
    const indexed = data.map((d, i) => ({ ...d, tradeNum: i + 1 }));
    const long = indexed
      .filter((d) => d.side?.toLowerCase() === "long" || d.side?.toLowerCase() === "buy")
      .map((d) => ({ x: d.tradeNum, y: d.pnl, ...d }));
    const short = indexed
      .filter((d) => d.side?.toLowerCase() === "short" || d.side?.toLowerCase() === "sell")
      .map((d) => ({ x: d.tradeNum, y: d.pnl, ...d }));
    return { long, short };
  }, [data]);

  if (!data || data.length === 0) return <EmptyPlaceholder />;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <ScatterChart margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid {...CHART_GRID_STYLE} />
        <XAxis
          dataKey="x"
          type="number"
          domain={["auto", "auto"]}
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          label={{ value: "交易序号", position: "insideBottomRight", offset: -4, fontSize: 9, fill: "var(--t3)" }}
        />
        <YAxis
          dataKey="y"
          type="number"
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          width={52}
          tickFormatter={(v) => `${Number(v).toFixed(0)}`}
        />
        <RechartsTooltip
          {...CHART_TOOLTIP_PROPS}
          formatter={(value: unknown, name: unknown) => [`${Number(value).toFixed(2)} USDT`, name === "y" ? "盈亏" : String(name)]}
        />
        <ReferenceLine y={0} {...CHART_REFERENCE_LINE} />
        <Legend
          wrapperStyle={CHART_LEGEND_STYLE}
          formatter={(value) => value === "long" ? "多头" : "空头"}
        />
        <Scatter name="long" data={chartData.long} fill={ACCENT_LONG} fillOpacity={0.7} r={3} />
        <Scatter name="short" data={chartData.short} fill={ACCENT_SHORT} fillOpacity={0.7} r={3} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

/* ------------------------------------------------------------------ */
/*  MAE/MFE Scatter Chart                                              */
/* ------------------------------------------------------------------ */

export function MaeMfeChart({ data }: { data?: MaeMfePoint[] }) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return { mae: [], mfe: [] };
    const mae = data.map((d) => ({ x: Math.abs(d.mae), y: d.pnl, win: d.pnl >= 0 }));
    const mfe = data.map((d) => ({ x: d.mfe, y: d.pnl, win: d.pnl >= 0 }));
    return { mae, mfe };
  }, [data]);

  if (!data || data.length === 0) return <EmptyPlaceholder />;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <ScatterChart margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid {...CHART_GRID_STYLE} />
        <XAxis
          dataKey="x"
          type="number"
          domain={["auto", "auto"]}
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          label={{ value: "MAE", position: "insideBottomRight", offset: -4, fontSize: 9, fill: "var(--t3)" }}
          tickFormatter={(v) => `${Number(v).toFixed(0)}`}
        />
        <YAxis
          dataKey="y"
          type="number"
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          width={52}
          tickFormatter={(v) => `${Number(v).toFixed(0)}`}
        />
        <RechartsTooltip
          {...CHART_TOOLTIP_PROPS}
          formatter={(value: unknown, name: unknown) => [`${Number(value).toFixed(2)}`, name === "y" ? "最终盈亏" : "MAE"]}
        />
        <ReferenceLine y={0} {...CHART_REFERENCE_LINE} />
        <Scatter
          name="mae"
          data={chartData.mae}
          isAnimationActive
          animationDuration={1200}
        >
          {chartData.mae.map((entry, i) => (
            <Cell key={i} fill={entry.win ? ACCENT_GREEN : ACCENT_RED} fillOpacity={0.7} />
          ))}
        </Scatter>
      </ScatterChart>
    </ResponsiveContainer>
  );
}

/* ------------------------------------------------------------------ */
/*  Holding Time Distribution Chart                                    */
/* ------------------------------------------------------------------ */

export function HoldingTimeChart({ data }: { data?: HoldingTimeBin[] }) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((b) => ({
      label: `${b.bin_start.toFixed(0)}h`,
      count: b.count,
    }));
  }, [data]);

  if (chartData.length === 0) return <EmptyPlaceholder />;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid {...CHART_GRID_STYLE} />
        <XAxis
          dataKey="label"
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          width={32}
        />
        <RechartsTooltip
          {...CHART_TOOLTIP_PROPS}
          formatter={(value: unknown) => [`${value} 笔`, "交易数"]}
        />
        <Bar dataKey="count" fill={CHART_COLORS.accent} fillOpacity={0.8} radius={[2, 2, 0, 0]} isAnimationActive animationDuration={1200} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ------------------------------------------------------------------ */
/*  Win/Loss Streak Chart                                              */
/* ------------------------------------------------------------------ */

export function StreakChart({ data }: { data?: StreakEntry[] }) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((s) => ({
      streak_num: s.streak_num,
      count: s.type === "win" ? s.count : -s.count,
      win: s.type === "win",
    }));
  }, [data]);

  if (chartData.length === 0) return <EmptyPlaceholder />;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid {...CHART_GRID_STYLE} />
        <XAxis
          dataKey="streak_num"
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          label={{ value: "连续序号", position: "insideBottomRight", offset: -4, fontSize: 9, fill: "var(--t3)" }}
        />
        <YAxis
          tick={CHART_AXIS_STYLE}
          tickLine={false}
          axisLine={false}
          width={32}
        />
        <RechartsTooltip
          {...CHART_TOOLTIP_PROPS}
          formatter={(value: unknown) => [`${Math.abs(Number(value))} 笔`, "连续长度"]}
        />
        <ReferenceLine y={0} {...CHART_REFERENCE_LINE} />
        <Bar dataKey="count" radius={[2, 2, 0, 0]} isAnimationActive animationDuration={1200}>
          {chartData.map((entry, i) => (
            <Cell key={i} fill={entry.win ? ACCENT_GREEN : ACCENT_RED} fillOpacity={0.8} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

/* ------------------------------------------------------------------ */
/*  Long vs Short Chart                                                */
/* ------------------------------------------------------------------ */

export function LongShortChart({ data }: { data?: LongVsShort }) {
  if (!data) return <EmptyPlaceholder />;

  const metrics = [
    { label: "交易笔数", long: data.long.trades, short: data.short.trades, fmt: (v: number) => `${v}` },
    { label: "总盈亏", long: data.long.total_pnl, short: data.short.total_pnl, fmt: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)} U` },
    { label: "平均盈亏", long: data.long.avg_pnl, short: data.short.avg_pnl, fmt: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)} U` },
    { label: "胜率", long: data.long.win_rate * 100, short: data.short.win_rate * 100, fmt: (v: number) => `${v.toFixed(1)}%` },
  ];

  return (
    <div className="grid grid-cols-2 gap-3">
      {metrics.map((m) => {
        const total = Math.abs(m.long) + Math.abs(m.short);
        const longPct = total > 0 ? (Math.abs(m.long) / total) * 100 : 50;
        return (
          <div key={m.label} className="rounded-lg border bg-card p-3">
            <div className={STAT_LABEL_CLS}>{m.label}</div>
            <div className="flex items-center justify-between text-xs mb-1">
              <span style={{ color: ACCENT_LONG }}>{m.fmt(m.long)}</span>
              <span style={{ color: ACCENT_SHORT }}>{m.fmt(m.short)}</span>
            </div>
            <div className="flex h-2 rounded-full overflow-hidden bg-secondary">
              <div style={{ width: `${longPct}%`, background: ACCENT_LONG, opacity: 0.8 }} />
              <div style={{ width: `${100 - longPct}%`, background: ACCENT_SHORT, opacity: 0.8 }} />
            </div>
            <div className="flex justify-between mt-1">
              <span className="text-[9px] text-qds-t3">多头</span>
              <span className="text-[9px] text-qds-t3">空头</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Return by Group (DOW / Hour) — Box-plot approximation              */
/* ------------------------------------------------------------------ */

export function ReturnByGroupChart({ data, labelKey }: {
  data?: ReturnByGroup[];
  labelKey: "dow_name" | "hour";
  title: string;
}) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];
    return data.map((g) => {
      const stats = boxStats(g.values);
      if (!stats) return null;
      const label = labelKey === "dow_name"
        ? (g.dow_name ?? `Day ${g.dow}`)
        : `${String(g.hour ?? 0).padStart(2, "0")}:00`;
      return {
        label,
        median: stats.median,
        q1: stats.q1,
        q3: stats.q3,
        min: stats.min,
        max: stats.max,
        errorLow: stats.median - stats.q1,
        errorHigh: stats.q3 - stats.median,
      };
    }).filter(Boolean) as Array<{
      label: string; median: number; q1: number; q3: number;
      min: number; max: number; errorLow: number; errorHigh: number;
    }>;
  }, [data, labelKey]);

  if (chartData.length === 0) return <EmptyPlaceholder />;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
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
          width={48}
          tickFormatter={(v) => `${Number(v).toFixed(1)}`}
        />
        <RechartsTooltip
          {...CHART_TOOLTIP_PROPS}
          formatter={(value: unknown) => [`${Number(value).toFixed(2)}`, "中位收益"]}
        />
        <ReferenceLine y={0} {...CHART_REFERENCE_LINE} />
        <Bar dataKey="median" radius={[2, 2, 0, 0]} isAnimationActive animationDuration={1200}>
          {chartData.map((entry, i) => (
            <Cell key={i} fill={entry.median >= 0 ? ACCENT_GREEN : ACCENT_RED} fillOpacity={0.8} />
          ))}
          <ErrorBar dataKey="errorHigh" width={4} strokeWidth={1.5} stroke="var(--t2)" direction="y" />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
