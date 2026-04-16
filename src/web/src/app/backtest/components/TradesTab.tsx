"use client";

import { useEffect, useState, useMemo } from "react";
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
import { HelpCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import { API_BASE } from "@/lib/api";
import { useCountUp } from "@/hooks/useCountUp";
import {
  CHART_TOOLTIP_PROPS,
  CHART_AXIS_STYLE,
  CHART_GRID_STYLE,
  CHART_REFERENCE_LINE,
  CHART_COLORS,
} from "@/lib/chartTheme";
import type {
  BacktestResult,
  TradePnlDistributionBin,
  CumulativeTradePnl,
  TradePnlScatterPoint,
  MaeMfePoint,
  HoldingTimeBin,
  StreakEntry,
  LongVsShort,
  ReturnByGroup,
} from "../types";

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const ACCENT_GREEN = CHART_COLORS.success;
const ACCENT_RED = CHART_COLORS.danger;
const ACCENT_LONG = CHART_COLORS.info;
const ACCENT_SHORT = CHART_COLORS.accent;


/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function HelpTip({ text }: { text: string }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="inline-flex items-center justify-center ml-1 cursor-help">
          <HelpCircle className="w-3 h-3 text-qds-t3 hover:text-muted-foreground transition-colors" />
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[220px] text-[11px] leading-relaxed">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}


function EmptyPlaceholder({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center h-full min-h-[120px]">
      <span className="text-xs text-qds-t3">{label ?? "暂无数据"}</span>
    </div>
  );
}

/** Compute Q1, median, Q3 from an array of numbers */
function boxStats(values: number[]): { min: number; q1: number; median: number; q3: number; max: number } | null {
  if (!values || values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const q = (p: number) => {
    const idx = p * (sorted.length - 1);
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
  };
  return { min: sorted[0], q1: q(0.25), median: q(0.5), q3: q(0.75), max: sorted[sorted.length - 1] };
}

/* ------------------------------------------------------------------ */
/*  KPI Metric Card                                                    */
/* ------------------------------------------------------------------ */

interface MetricCardProps {
  label: string;
  tooltip?: string;
  value: number | null | undefined;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  showSign?: boolean;
  positive?: boolean | null;
  index: number;
}

function MetricCard({
  label,
  tooltip,
  value,
  decimals = 2,
  prefix = "",
  suffix = "",
  showSign = false,
  positive,
  index,
}: MetricCardProps) {
  const numeric = value ?? 0;
  const animated = useCountUp(numeric, 800 + index * 80, value != null);

  const colorClass =
    positive == null
      ? "text-foreground"
      : positive
        ? "text-qds-success"
        : "text-destructive";

  const accentColor =
    positive == null
      ? "var(--info)"
      : positive
        ? "var(--suc)"
        : "var(--dan)";

  const formatted =
    value == null
      ? "N/A"
      : decimals === 0
        ? `${prefix}${Math.round(animated).toLocaleString()}${suffix}`
        : showSign
          ? `${prefix}${animated >= 0 ? "+" : ""}${animated.toFixed(decimals)}${suffix}`
          : `${prefix}${animated.toFixed(decimals)}${suffix}`;

  return (
    <div
      className="group relative flex flex-col gap-2.5 rounded-xl border bg-card p-4 hover:bg-secondary transition-all duration-300 overflow-hidden"
    >
      <div
        className="absolute bottom-0 left-0 right-0 h-[2px] opacity-30 group-hover:opacity-70 transition-opacity duration-500"
        style={{ background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)` }}
      />
      <span className="sc-l inline-flex items-center">
        {label}
        {tooltip && <HelpTip text={tooltip} />}
      </span>
      <span className={`text-2xl font-bold font-mono tracking-tight leading-none ${colorClass}`}>
        {formatted}
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Chart Section Title                                                */
/* ------------------------------------------------------------------ */

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <span className="sc-l">
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Trade PnL Distribution Chart                                       */
/* ------------------------------------------------------------------ */

function PnlDistributionChart({ data }: { data?: TradePnlDistributionBin[] }) {
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

function CumulativePnlChart({ data }: { data?: CumulativeTradePnl[] }) {
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

function PnlScatterChart({ data }: { data?: TradePnlScatterPoint[] }) {
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
          wrapperStyle={{ fontSize: 10, color: "var(--t2)" }}
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

function MaeMfeChart({ data }: { data?: MaeMfePoint[] }) {
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

function HoldingTimeChart({ data }: { data?: HoldingTimeBin[] }) {
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

function StreakChart({ data }: { data?: StreakEntry[] }) {
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

function LongShortChart({ data }: { data?: LongVsShort }) {
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
            <div className="sc-l">{m.label}</div>
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

function ReturnByGroupChart({ data, labelKey, title }: {
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

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

interface TradesTabProps {
  runId: string;
}

export function TradesTab({ runId }: TradesTabProps) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setResult(null);

    fetch(`${API_BASE}/api/backtest/${runId}/result`, {
      headers: {
        ...(process.env.NEXT_PUBLIC_API_KEY
          ? { "X-API-Key": process.env.NEXT_PUBLIC_API_KEY }
          : {}),
      },
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) setResult(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex flex-col gap-4 p-5">
        <div className="grid grid-cols-4 gap-3">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-xl" />
          ))}
        </div>
        <div className="grid grid-cols-2 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-xl" />
          ))}
        </div>
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-destructive">{error ?? "加载失败"}</span>
      </div>
    );
  }

  const s = result.statistics;

  return (
    <div className="flex flex-col gap-4 p-5">

      {/* ── KPI Grid 4×3 ── */}
      <div className="grid grid-cols-4 gap-3">
        {/* Row 1 */}
        <MetricCard
          label="中位交易盈亏"
          tooltip="所有已平仓交易盈亏的中位数，比均值更能反映典型单笔表现"
          value={s.median_trade_pnl}
          showSign
          positive={s.median_trade_pnl != null ? s.median_trade_pnl >= 0 : null}
          suffix=" U"
          index={0}
        />
        <MetricCard
          label="盈亏标准差"
          tooltip="单笔交易盈亏的标准差，衡量收益的离散程度（波动性）"
          value={s.std_trade_pnl}
          positive={null}
          suffix=" U"
          index={1}
        />
        <MetricCard
          label="成交率"
          tooltip="实际成交订单数占提交订单总数的百分比"
          value={s.fill_rate}
          suffix="%"
          positive={s.fill_rate != null ? s.fill_rate >= 90 : null}
          index={2}
        />
        <MetricCard
          label="日均交易"
          tooltip="每个交易日平均完成的交易笔数"
          value={s.avg_trades_per_day}
          positive={null}
          index={3}
        />
        {/* Row 2 */}
        <MetricCard
          label="恢复系数"
          tooltip="净利润除以最大回撤绝对值，衡量策略从亏损中恢复的能力，> 3 为优秀"
          value={s.recovery_factor}
          positive={s.recovery_factor != null ? s.recovery_factor >= 3 : null}
          index={4}
        />
        <MetricCard
          label="SQN"
          tooltip="系统质量数 (System Quality Number)，> 2 可接受，> 3 优秀，> 5 卓越"
          value={s.sqn}
          positive={s.sqn != null ? s.sqn >= 2 : null}
          index={5}
        />
        <MetricCard
          label="凯利比例"
          tooltip="凯利准则建议的最优仓位比例，实际使用时通常取半凯利"
          value={s.kelly_criterion}
          suffix="%"
          positive={s.kelly_criterion != null ? s.kelly_criterion > 0 : null}
          index={6}
        />
        <MetricCard
          label="K-Ratio"
          tooltip="衡量权益曲线增长一致性的指标，越高代表越稳定的上升趋势"
          value={s.k_ratio}
          positive={s.k_ratio != null ? s.k_ratio > 0 : null}
          index={7}
        />
        {/* Row 3 */}
        <MetricCard
          label="期望值 (R)"
          tooltip="以初始风险 R 为单位的平均期望收益，> 0.2R 为较好策略"
          value={s.expectancy_r}
          showSign
          positive={s.expectancy_r != null ? s.expectancy_r >= 0 : null}
          suffix="R"
          index={8}
        />
        <MetricCard
          label="总交易笔数"
          tooltip="回测期间完成平仓的总交易笔数"
          value={s.total_trades}
          decimals={0}
          positive={null}
          index={9}
        />
        <MetricCard
          label="最大盈利"
          tooltip="单笔最大盈利金额"
          value={s.largest_win}
          positive={null}
          showSign
          suffix=" U"
          index={10}
        />
        <MetricCard
          label="最大亏损"
          tooltip="单笔最大亏损金额（绝对值）"
          value={s.largest_loss != null ? Math.abs(s.largest_loss) : null}
          prefix="-"
          positive={null}
          suffix=" U"
          index={11}
        />
      </div>

      {/* ── Trade PnL ── */}
      <span className="qds-section-label">Trade PnL</span>
      <div className="grid grid-cols-2 gap-4">
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>盈亏分布</SectionTitle>
          <PnlDistributionChart data={result.trade_pnl_distribution} />
        </div></div>
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>累积盈亏</SectionTitle>
          <CumulativePnlChart data={result.cumulative_trade_pnl} />
        </div></div>
      </div>

      {/* ── Scatter & MAE/MFE ── */}
      <span className="qds-section-label">Scatter &amp; MAE / MFE</span>
      <div className="grid grid-cols-2 gap-4">
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>逐笔盈亏散点</SectionTitle>
          <PnlScatterChart data={result.trade_pnl_scatter} />
        </div></div>
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>MAE/MFE 分析</SectionTitle>
          <MaeMfeChart data={result.mae_mfe} />
        </div></div>
      </div>

      {/* ── Patterns & Streaks ── */}
      <span className="qds-section-label">Patterns &amp; Streaks</span>
      <div className="grid grid-cols-2 gap-4">
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>持仓时长分布</SectionTitle>
          <HoldingTimeChart data={result.holding_time_distribution} />
        </div></div>
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>连盈/连亏序列</SectionTitle>
          <StreakChart data={result.streak_sequence} />
        </div></div>
      </div>

      {/* ── Long vs Short ── */}
      <span className="qds-section-label">Long vs Short</span>
      <div className="bt-cd"><div className="bt-cd-body">
        <LongShortChart data={result.long_vs_short} />
      </div></div>

      {/* ── By Time ── */}
      <span className="qds-section-label">By Time</span>
      <div className="grid grid-cols-2 gap-4">
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>按星期收益分布</SectionTitle>
          <ReturnByGroupChart
            data={result.return_by_dow}
            labelKey="dow_name"
            title="按星期"
          />
        </div></div>
        <div className="bt-cd"><div className="bt-cd-body">
          <SectionTitle>按小时收益分布</SectionTitle>
          <ReturnByGroupChart
            data={result.return_by_hour}
            labelKey="hour"
            title="按小时"
          />
        </div></div>
      </div>
    </div>
  );
}
