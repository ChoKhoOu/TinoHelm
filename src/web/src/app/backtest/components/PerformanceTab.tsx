"use client";

import { useEffect, useState, useMemo } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
  LineChart,
  Line,
  BarChart,
  Bar,
  Cell,
  ScatterChart,
  Scatter,
  ComposedChart,
} from "recharts";
import { motion } from "framer-motion";
import { HelpCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { API_BASE } from "@/lib/api";
import { useCountUp } from "@/hooks/useCountUp";
import type {
  BacktestResult,
  AnnualReturn,
  RollingReturnPoint,
  DistributionBin,
  QQPlotPoint,
  BenchmarkPoint,
  DrawdownPeriod,
  RollingSharpePoint,
  RollingSortinoPoint,
  RollingVolatilityPoint,
  RollingBetaPoint,
} from "../types";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const clamp = (v: number, min: number, max: number) =>
  Math.max(min, Math.min(max, v));

function downsample<T>(arr: T[], maxPoints: number): T[] {
  if (arr.length <= maxPoints) return arr;
  const step = arr.length / maxPoints;
  return Array.from({ length: maxPoints }, (_, i) => arr[Math.round(i * step)]);
}

function fmtDate(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString("zh-CN", {
      month: "short",
      day: "numeric",
    });
  } catch {
    return ts.slice(5, 10);
  }
}

function fmtDateFull(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return ts.slice(0, 10);
  }
}

const tooltipStyle: React.CSSProperties = {
  background: "rgba(15, 20, 25, 0.95)",
  border: "1px solid rgba(255,255,255,0.08)",
  borderRadius: 10,
  fontSize: 11,
  color: "#E8EAED",
  backdropFilter: "blur(8px)",
  boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
};
const tooltipLabelStyle: React.CSSProperties = { color: "rgba(255,255,255,0.5)" };
const tooltipItemStyle: React.CSSProperties = { color: "#E8EAED" };


function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "N/A";
  return v.toFixed(decimals);
}

/* ------------------------------------------------------------------ */
/*  HelpTip                                                            */
/* ------------------------------------------------------------------ */

function HelpTip({ text }: { text: string }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="inline-flex items-center justify-center ml-1 cursor-help">
          <HelpCircle className="w-3 h-3 text-muted-foreground/30 hover:text-muted-foreground/60 transition-colors" />
        </TooltipTrigger>
        <TooltipContent
          side="top"
          className="max-w-[240px] text-[11px] leading-relaxed"
        >
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

/* ------------------------------------------------------------------ */
/*  GlassCard                                                          */
/* ------------------------------------------------------------------ */

function GlassCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-sm overflow-hidden ${className}`}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SectionHeader                                                      */
/* ------------------------------------------------------------------ */

function SectionHeader({
  title,
  index,
}: {
  title: string;
  index: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{
        duration: 0.4,
        delay: index * 0.06,
        ease: [0.22, 1, 0.36, 1],
      }}
      className="flex items-center gap-2"
    >
      <div className="w-1 h-4 rounded-full bg-[var(--accent-blue)] opacity-70" />
      <span className="text-xs font-semibold tracking-[1.2px] uppercase text-muted-foreground/60">
        {title}
      </span>
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  MetricCard                                                         */
/* ------------------------------------------------------------------ */

interface MetricCardProps {
  label: string;
  sublabel?: string;
  tooltip: string;
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
  sublabel,
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
    positive === null || positive === undefined
      ? "text-foreground/90"
      : positive
        ? "text-[var(--accent-green)]"
        : "text-[var(--accent-red)]";

  const accentColor =
    positive === null || positive === undefined
      ? "rgba(76, 158, 235, 0.5)"
      : positive
        ? "rgba(38, 217, 127, 0.5)"
        : "rgba(239, 83, 80, 0.5)";

  const formatted =
    value == null
      ? "N/A"
      : decimals === 0
        ? `${prefix}${Math.round(animated).toLocaleString()}${suffix}`
        : showSign
          ? `${prefix}${animated >= 0 ? "+" : ""}${animated.toFixed(decimals)}${suffix}`
          : `${prefix}${animated.toFixed(decimals)}${suffix}`;

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.45,
        delay: 0.1 + index * 0.06,
        ease: [0.22, 1, 0.36, 1],
      }}
      className="group relative flex flex-col gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-sm p-4 hover:bg-white/[0.05] transition-all duration-300 overflow-hidden"
    >
      <div
        className="absolute bottom-0 left-0 right-0 h-[2px] opacity-30 group-hover:opacity-70 transition-opacity duration-500"
        style={{
          background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`,
        }}
      />
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 inline-flex items-center">
        {label}
        <HelpTip text={tooltip} />
      </span>
      <span
        className={`text-2xl font-bold font-heading tracking-tight leading-none ${colorClass}`}
      >
        {formatted}
      </span>
      {sublabel && (
        <span className="text-[9px] text-muted-foreground/40 leading-tight">
          {sublabel}
        </span>
      )}
    </motion.div>
  );
}

/* ------------------------------------------------------------------ */
/*  ChartPlaceholder                                                   */
/* ------------------------------------------------------------------ */

function ChartPlaceholder({ message = "暂无数据" }: { message?: string }) {
  return (
    <div className="flex items-center justify-center h-full min-h-[120px]">
      <span className="text-xs text-muted-foreground/40">{message}</span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Toggle pill button (inline, no toggle-group dep)                   */
/* ------------------------------------------------------------------ */

function TogglePill({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`text-[9px] px-2 py-0.5 rounded border transition-all duration-200 ${
        active
          ? "border-[var(--accent-blue)] text-[var(--accent-blue)] bg-[var(--accent-blue)]/10"
          : "border-white/[0.08] text-muted-foreground/40 hover:text-muted-foreground/70"
      }`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Rolling chart legend                                               */
/* ------------------------------------------------------------------ */

function RollingLegend({ items }: { items: { color: string; label: string }[] }) {
  return (
    <div className="flex items-center gap-3 text-[9px] text-muted-foreground/50">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1">
          <span
            className="inline-block w-3 h-[2px] rounded"
            style={{ background: item.color }}
          />
          {item.label}
        </span>
      ))}
    </div>
  );
}

/* ================================================================== */
/*  Section 2.1: 权益表现 Charts                                      */
/* ================================================================== */

function EnhancedEquityCurve({
  equityCurve,
  benchmarkCurve,
  startingBalance,
}: {
  equityCurve: BacktestResult["equity_curve"];
  benchmarkCurve?: BenchmarkPoint[];
  startingBalance: number;
}) {
  const [mode, setMode] = useState<"$" | "%">("$");
  const [scaleMode, setScaleMode] = useState<"linear" | "log">("linear");
  const [showBM, setShowBM] = useState(true);

  const hasBenchmarkData = (benchmarkCurve?.length ?? 0) > 0;
  const bmStart = benchmarkCurve?.[0]?.equity ?? startingBalance;

  const chartData = useMemo(() => {
    const sampled = downsample(equityCurve, 500);
    const bmMap = new Map(benchmarkCurve?.map((b) => [b.timestamp, b.equity]) ?? []);

    return sampled.map((p) => {
      const ts = p.timestamp ?? p.date ?? "";
      const bmEquity = bmMap.get(ts) ?? null;

      if (mode === "%") {
        return {
          t: fmtDate(ts),
          value: parseFloat(((p.equity / startingBalance - 1) * 100).toFixed(4)),
          benchmark:
            bmEquity != null && bmStart > 0
              ? parseFloat(((bmEquity / bmStart - 1) * 100).toFixed(4))
              : null,
        };
      }
      return {
        t: fmtDate(ts),
        value: p.equity,
        benchmark: bmEquity,
      };
    });
  }, [equityCurve, benchmarkCurve, startingBalance, bmStart, mode]);

  if (chartData.length < 2) return <ChartPlaceholder />;

  const values = chartData.map((d) => d.value);
  const minVal = Math.min(...values);
  const maxVal = Math.max(...values);
  const range = maxVal - minVal || 1;
  const refValue = mode === "%" ? 0 : startingBalance;
  const balanceStop = clamp((maxVal - refValue) / range, 0.01, 0.99);

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          权益曲线
        </span>
        <div className="flex items-center gap-1">
          <TogglePill active={mode === "$"} onClick={() => setMode("$")}>$</TogglePill>
          <TogglePill active={mode === "%"} onClick={() => setMode("%")}>%</TogglePill>
          <div className="w-px h-3 bg-white/[0.08] mx-1" />
          <TogglePill active={scaleMode === "linear"} onClick={() => setScaleMode("linear")}>Linear</TogglePill>
          <TogglePill active={scaleMode === "log"} onClick={() => setScaleMode("log")}>Log</TogglePill>
          {hasBenchmarkData && (
            <>
              <div className="w-px h-3 bg-white/[0.08] mx-1" />
              <TogglePill active={showBM} onClick={() => setShowBM(!showBM)}>BM</TogglePill>
            </>
          )}
        </div>
      </div>
      {/* Legend */}
      <div className="flex items-center gap-4 text-[9px] text-muted-foreground/50 px-1">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 h-[2px] rounded" style={{ background: "linear-gradient(90deg, #4C9EEB, #A78BFA)" }} />
          策略 (盈利)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 h-[2px] rounded" style={{ background: "#EF5350" }} />
          策略 (亏损)
        </span>
        {hasBenchmarkData && showBM && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-4 h-[2px] rounded border-t border-dashed" style={{ borderColor: "#8B949E" }} />
            Benchmark (B&H)
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 h-[2px] rounded" style={{ background: "#F0B429", opacity: 0.4 }} />
          {mode === "%" ? "零线" : "初始资金"}
        </span>
      </div>
      <motion.div
        key={`${mode}-${scaleMode}`}
        initial={{ opacity: 0.5, scale: 0.995 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
        style={{ width: "100%", height: 260 }}
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chartData}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
          >
          <defs>
            <linearGradient id="perfEqStroke" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#A78BFA" />
              <stop offset={balanceStop * 0.6} stopColor="#4C9EEB" />
              <stop offset={balanceStop} stopColor="#4C9EEB" />
              <stop offset={balanceStop} stopColor="#EF5350" />
              <stop offset="100%" stopColor="#EF5350" />
            </linearGradient>
            <linearGradient id="perfEqFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4C9EEB" stopOpacity={0.2} />
              <stop offset="100%" stopColor="#4C9EEB" stopOpacity={0} />
            </linearGradient>
            <filter id="perfEqGlow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="4" />
            </filter>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="t"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            scale={scaleMode}
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) =>
              mode === "%"
                ? `${Number(v).toFixed(0)}%`
                : `$${(Number(v) / 1000).toFixed(Number(v) >= 10000 ? 0 : 1)}k`
            }
            width={48}
            domain={scaleMode === "log" ? ["auto", "auto"] : undefined}
          />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              if (name === "benchmark") {
                return [
                  <span key="v" style={{ color: "#8B949E" }}>
                    {mode === "%"
                      ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`
                      : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                  </span>,
                  "基准 (B&H)",
                ];
              }
              const color = mode === "%" ? (v >= 0 ? "#4C9EEB" : "#EF5350") : (v >= startingBalance ? "#4C9EEB" : "#EF5350");
              return [
                <span key="v" style={{ color }}>
                  {mode === "%"
                    ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`
                    : `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                </span>,
                "策略",
              ];
            }}
          />
          <ReferenceLine
            y={refValue}
            stroke="#F0B429"
            strokeDasharray="6 4"
            strokeWidth={1}
            strokeOpacity={0.4}
          />
          {/* Glow layer */}
          <Area
            type="monotone"
            dataKey="value"
            stroke="url(#perfEqStroke)"
            strokeWidth={5}
            fill="none"
            dot={false}
            filter="url(#perfEqGlow)"
            opacity={0.25}
            isAnimationActive={false}
            tooltipType="none"
          />
          {/* Main strategy line */}
          <Area
            type="monotone"
            dataKey="value"
            stroke="url(#perfEqStroke)"
            strokeWidth={1.5}
            fill="url(#perfEqFill)"
            dot={false}
            activeDot={{
              r: 4,
              fill: "#4C9EEB",
              stroke: "rgba(76,158,235,0.3)",
              strokeWidth: 6,
            }}
            isAnimationActive
            animationDuration={1600}
            animationEasing="ease-in-out"
          />
          {/* Benchmark line */}
          {hasBenchmarkData && showBM && (
            <Line
              type="monotone"
              dataKey="benchmark"
              stroke="#8B949E"
              strokeWidth={1}
              strokeDasharray="6 4"
              dot={false}
              connectNulls={true}
              isAnimationActive
              animationDuration={1600}
              animationEasing="ease-in-out"
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
      </motion.div>
    </GlassCard>
  );
}

function DrawdownChart({
  equityCurve,
}: {
  equityCurve: BacktestResult["equity_curve"];
}) {
  const chartData = useMemo(() => {
    const sampled = downsample(equityCurve, 500);
    return sampled.map((p) => ({
      t: fmtDate(p.timestamp ?? p.date ?? ""),
      drawdown: p.drawdown_pct ?? 0,
    }));
  }, [equityCurve]);

  if (chartData.length < 2) return <ChartPlaceholder />;

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
        水下回撤曲线
      </span>
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart
          data={chartData}
          margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
        >
          <defs>
            <linearGradient id="perfDdGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#EF5350" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#EF5350" stopOpacity={0.02} />
            </linearGradient>
            <filter id="perfDdGlow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur in="SourceGraphic" stdDeviation="3" />
            </filter>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="t"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            width={42}
          />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown) => [
              <span key="v" style={{ color: "#EF5350" }}>
                {Number(value).toFixed(2)}%
              </span>,
              "回撤",
            ]}
          />
          {/* Glow layer */}
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="#EF5350"
            strokeWidth={4}
            fill="none"
            dot={false}
            filter="url(#perfDdGlow)"
            opacity={0.25}
            isAnimationActive={false}
            tooltipType="none"
          />
          {/* Main line */}
          <Area
            type="monotone"
            dataKey="drawdown"
            stroke="#EF5350"
            strokeWidth={1.2}
            fill="url(#perfDdGrad)"
            dot={false}
            isAnimationActive={true}
            animationDuration={1600}
            animationEasing="ease-in-out"
          />
        </AreaChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

function Top5DrawdownsTable({
  drawdownPeriods,
}: {
  drawdownPeriods?: DrawdownPeriod[];
}) {
  const top5 = useMemo(
    () => (drawdownPeriods ?? []).slice(0, 5),
    [drawdownPeriods]
  );

  if (!top5.length) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          Top 5 最大回撤
        </span>
        <ChartPlaceholder />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4">
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-3">
        Top 5 最大回撤
      </span>
      <Table>
        <TableHeader>
          <TableRow className="border-white/[0.06] hover:bg-transparent">
            <TableHead className="text-[9px] text-muted-foreground/50 font-semibold h-8">#</TableHead>
            <TableHead className="text-[9px] text-muted-foreground/50 font-semibold h-8">开始日期</TableHead>
            <TableHead className="text-[9px] text-muted-foreground/50 font-semibold h-8">谷底日期</TableHead>
            <TableHead className="text-[9px] text-muted-foreground/50 font-semibold h-8 text-right">深度</TableHead>
            <TableHead className="text-[9px] text-muted-foreground/50 font-semibold h-8 text-right">持续 (天)</TableHead>
            <TableHead className="text-[9px] text-muted-foreground/50 font-semibold h-8 text-right">恢复 (天)</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {top5.map((dd, i) => (
            <TableRow key={i} className="border-white/[0.04] hover:bg-white/[0.03]">
              <TableCell className="text-[10px] text-muted-foreground/60 py-2">{i + 1}</TableCell>
              <TableCell className="text-[10px] text-muted-foreground/70 py-2">{fmtDateFull(dd.start)}</TableCell>
              <TableCell className="text-[10px] text-muted-foreground/70 py-2">{fmtDateFull(dd.trough_date)}</TableCell>
              <TableCell className="text-[10px] text-[var(--accent-red)] font-medium py-2 text-right">
                {dd.max_drawdown_pct.toFixed(2)}%
              </TableCell>
              <TableCell className="text-[10px] text-muted-foreground/70 py-2 text-right">{dd.duration_days}</TableCell>
              <TableCell className="text-[10px] text-muted-foreground/70 py-2 text-right">
                {dd.recovery_days != null ? dd.recovery_days : "—"}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </GlassCard>
  );
}

/* ================================================================== */
/*  Section 2.2: 周期收益 Charts                                      */
/* ================================================================== */

function MonthlyHeatmap({
  monthlyReturns,
}: {
  monthlyReturns?: Array<{ period: string; return_pct: number }>;
}) {
  const { years, months, grid, maxAbs } = useMemo(() => {
    if (!monthlyReturns?.length) {
      return { years: [] as number[], months: [] as number[], grid: new Map<string, number>(), maxAbs: 1 };
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
    const months = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
    const maxAbs = Math.max(1, ...Array.from(grid.values()).map(Math.abs));

    return { years, months, grid, maxAbs };
  }, [monthlyReturns]);

  const monthLabels = [
    "1月", "2月", "3月", "4月", "5月", "6月",
    "7月", "8月", "9月", "10月", "11月", "12月",
  ];

  if (!years.length) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-3">
          月度收益热力图
        </span>
        <ChartPlaceholder />
      </GlassCard>
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

  function textColor(val: number | undefined): string {
    if (val == null) return "rgba(255,255,255,0.2)";
    return val >= 0 ? "#26D97F" : "#EF5350";
  }

  return (
    <GlassCard className="p-4">
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-3">
        月度收益热力图
      </span>
      <div className="overflow-x-auto">
        <div
          className="grid gap-[2px] text-[8px]"
          style={{
            gridTemplateColumns: `40px repeat(12, minmax(0, 1fr))`,
            minWidth: 520,
          }}
        >
          <div />
          {monthLabels.map((m) => (
            <div
              key={m}
              className="text-center text-muted-foreground/40 font-medium pb-1"
            >
              {m}
            </div>
          ))}
          {years.map((year) => (
            <div key={`row-${year}`} className="contents">
              <div
                className="flex items-center text-muted-foreground/50 font-medium pr-1"
                style={{ fontSize: 9 }}
              >
                {year}
              </div>
              {months.map((mo) => {
                const val = grid.get(`${year}-${mo}`);
                return (
                  <div
                    key={`${year}-${mo}`}
                    title={val != null ? `${val > 0 ? "+" : ""}${val.toFixed(2)}%` : "—"}
                    className="rounded flex items-center justify-center h-7 transition-all duration-200 hover:brightness-125 cursor-default"
                    style={{ background: cellColor(val) }}
                  >
                    <span
                      style={{
                        color: textColor(val),
                        fontSize: 8,
                        fontWeight: 600,
                      }}
                    >
                      {val != null
                        ? `${val > 0 ? "+" : ""}${val.toFixed(1)}%`
                        : ""}
                    </span>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </GlassCard>
  );
}

function AnnualReturnsChart({
  annualReturns,
}: {
  annualReturns?: AnnualReturn[];
}) {
  if (!annualReturns?.length) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          年度收益
        </span>
        <ChartPlaceholder />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
        年度收益
      </span>
      <ResponsiveContainer width="100%" height={180}>
        <BarChart
          data={annualReturns}
          margin={{ top: 8, right: 8, left: 8, bottom: 4 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="year"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            width={42}
          />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            formatter={(value: unknown) => {
              const v = Number(value);
              return [
                <span key="v" style={{ color: v >= 0 ? "#26D97F" : "#EF5350" }}>
                  {v >= 0 ? "+" : ""}{v.toFixed(2)}%
                </span>,
                "年度收益",
              ];
            }}
          />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeWidth={1} />
          <Bar dataKey="return_pct" radius={[3, 3, 0, 0]}>
            {annualReturns.map((entry, index) => (
              <Cell
                key={`ar-${index}`}
                fill={entry.return_pct >= 0 ? "#26D97F" : "#EF5350"}
                fillOpacity={0.8}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

function WeeklyReturnsChart({
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
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          周度收益
        </span>
        <ChartPlaceholder />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
        周度收益
      </span>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart
          data={data}
          margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="t"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
            width={38}
          />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            cursor={{ fill: "rgba(255,255,255,0.04)" }}
            formatter={(value: unknown) => {
              const v = Number(value);
              return [
                <span key="v" style={{ color: v >= 0 ? "#26D97F" : "#EF5350" }}>
                  {v >= 0 ? "+" : ""}{v.toFixed(2)}%
                </span>,
                "周收益",
              ];
            }}
          />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeWidth={1} />
          <Bar dataKey="return_pct" radius={[2, 2, 0, 0]}>
            {data.map((entry, index) => (
              <Cell
                key={`wr-${index}`}
                fill={entry.return_pct >= 0 ? "#26D97F" : "#EF5350"}
                fillOpacity={0.75}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

/* ================================================================== */
/*  Section 2.3: 滚动分析 Charts                                      */
/* ================================================================== */

function RollingSharpeChart({
  data: rawData,
}: {
  data?: RollingSharpePoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_3m: r.rolling_3m,
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some(
    (d) => d.rolling_3m != null || d.rolling_6m != null || d.rolling_12m != null
  );

  if (!hasData) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          滚动 Sharpe 比率
        </span>
        <ChartPlaceholder message="数据不足（需要 3 个月以上）" />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          滚动 Sharpe 比率
        </span>
        <RollingLegend items={[
          { color: "#4C9EEB", label: "3m" },
          { color: "#A78BFA", label: "6m" },
          { color: "#F0B429", label: "12m" },
        ]} />
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="t"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            width={36}
          />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              const colorMap: Record<string, string> = {
                rolling_3m: "#4C9EEB", rolling_6m: "#A78BFA", rolling_12m: "#F0B429",
              };
              const labelMap: Record<string, string> = {
                rolling_3m: "3m Sharpe", rolling_6m: "6m Sharpe", rolling_12m: "12m Sharpe",
              };
              const key = String(name);
              return [
                <span key="v" style={{ color: colorMap[key] ?? "#E8EAED" }}>
                  {v.toFixed(3)}
                </span>,
                labelMap[key] ?? key,
              ];
            }}
          />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeWidth={1} />
          <Line type="monotone" dataKey="rolling_3m" stroke="#4C9EEB" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
          <Line type="monotone" dataKey="rolling_6m" stroke="#A78BFA" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
          <Line type="monotone" dataKey="rolling_12m" stroke="#F0B429" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
        </LineChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

function RollingSortinoChart({
  data: rawData,
}: {
  data?: RollingSortinoPoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some((d) => d.rolling_6m != null || d.rolling_12m != null);

  if (!hasData) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          滚动 Sortino 比率
        </span>
        <ChartPlaceholder message="数据不足（需要 6 个月以上）" />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          滚动 Sortino 比率
        </span>
        <RollingLegend items={[
          { color: "#A78BFA", label: "6m" },
          { color: "#F0B429", label: "12m" },
        ]} />
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="t" tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} width={36} />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              const colorMap: Record<string, string> = { rolling_6m: "#A78BFA", rolling_12m: "#F0B429" };
              const labelMap: Record<string, string> = { rolling_6m: "6m Sortino", rolling_12m: "12m Sortino" };
              const key = String(name);
              return [
                <span key="v" style={{ color: colorMap[key] ?? "#E8EAED" }}>{v.toFixed(3)}</span>,
                labelMap[key] ?? key,
              ];
            }}
          />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeWidth={1} />
          <Line type="monotone" dataKey="rolling_6m" stroke="#A78BFA" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
          <Line type="monotone" dataKey="rolling_12m" stroke="#F0B429" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
        </LineChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

function RollingVolatilityChart({
  data: rawData,
}: {
  data?: RollingVolatilityPoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some((d) => d.rolling_6m != null || d.rolling_12m != null);

  if (!hasData) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          滚动波动率
        </span>
        <ChartPlaceholder message="数据不足（需要 6 个月以上）" />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          滚动波动率
        </span>
        <RollingLegend items={[
          { color: "#A78BFA", label: "6m" },
          { color: "#F0B429", label: "12m" },
        ]} />
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
          <defs>
            <linearGradient id="perfVol6mFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#A78BFA" stopOpacity={0.15} />
              <stop offset="100%" stopColor="#A78BFA" stopOpacity={0.02} />
            </linearGradient>
            <linearGradient id="perfVol12mFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#F0B429" stopOpacity={0.12} />
              <stop offset="100%" stopColor="#F0B429" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="t" tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`} width={42} />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              const colorMap: Record<string, string> = { rolling_6m: "#A78BFA", rolling_12m: "#F0B429" };
              const labelMap: Record<string, string> = { rolling_6m: "6m 波动率", rolling_12m: "12m 波动率" };
              const key = String(name);
              return [
                <span key="v" style={{ color: colorMap[key] ?? "#E8EAED" }}>{(v * 100).toFixed(2)}%</span>,
                labelMap[key] ?? key,
              ];
            }}
          />
          <Area type="monotone" dataKey="rolling_6m" stroke="#A78BFA" strokeWidth={1.5} fill="url(#perfVol6mFill)" dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
          <Area type="monotone" dataKey="rolling_12m" stroke="#F0B429" strokeWidth={1.5} fill="url(#perfVol12mFill)" dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
        </AreaChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

function RollingBetaChart({
  data: rawData,
}: {
  data?: RollingBetaPoint[];
}) {
  const data = useMemo(() => {
    if (!rawData?.length) return [];
    return downsample(rawData, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rawData]);

  const hasData = data.some((d) => d.rolling_6m != null || d.rolling_12m != null);

  if (!hasData) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          滚动 Beta
        </span>
        <ChartPlaceholder message="数据不足或无基准" />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          滚动 Beta
        </span>
        <RollingLegend items={[
          { color: "#A78BFA", label: "6m" },
          { color: "#F0B429", label: "12m" },
        ]} />
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="t" tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} width={36} />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              const colorMap: Record<string, string> = { rolling_6m: "#A78BFA", rolling_12m: "#F0B429" };
              const labelMap: Record<string, string> = { rolling_6m: "6m Beta", rolling_12m: "12m Beta" };
              const key = String(name);
              return [
                <span key="v" style={{ color: colorMap[key] ?? "#E8EAED" }}>{v.toFixed(3)}</span>,
                labelMap[key] ?? key,
              ];
            }}
          />
          <ReferenceLine y={1} stroke="#F0B429" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.4} />
          <Line type="monotone" dataKey="rolling_6m" stroke="#A78BFA" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
          <Line type="monotone" dataKey="rolling_12m" stroke="#F0B429" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
        </LineChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

function RollingReturnsChart({
  rollingReturns,
}: {
  rollingReturns?: RollingReturnPoint[];
}) {
  const data = useMemo(() => {
    if (!rollingReturns?.length) return [];
    return downsample(rollingReturns, 500).map((r) => ({
      t: fmtDate(r.timestamp),
      rolling_3m: r.rolling_3m,
      rolling_6m: r.rolling_6m,
      rolling_12m: r.rolling_12m,
    }));
  }, [rollingReturns]);

  const hasData = data.some(
    (d) => d.rolling_3m != null || d.rolling_6m != null || d.rolling_12m != null
  );

  if (!hasData) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          滚动收益 (3m / 6m / 12m)
        </span>
        <ChartPlaceholder message="数据不足（需要 3 个月以上）" />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          滚动收益
        </span>
        <RollingLegend items={[
          { color: "#4C9EEB", label: "3m" },
          { color: "#A78BFA", label: "6m" },
          { color: "#F0B429", label: "12m" },
        ]} />
      </div>
      <ResponsiveContainer width="100%" height={180}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis dataKey="t" tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
          <YAxis tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={(v) => `${Number(v).toFixed(0)}%`} width={42} />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              const colorMap: Record<string, string> = {
                rolling_3m: "#4C9EEB", rolling_6m: "#A78BFA", rolling_12m: "#F0B429",
              };
              const labelMap: Record<string, string> = {
                rolling_3m: "3m 滚动", rolling_6m: "6m 滚动", rolling_12m: "12m 滚动",
              };
              const key = String(name);
              return [
                <span key="v" style={{ color: colorMap[key] ?? "#E8EAED" }}>
                  {v >= 0 ? "+" : ""}{v.toFixed(2)}%
                </span>,
                labelMap[key] ?? key,
              ];
            }}
          />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeWidth={1} />
          <Line type="monotone" dataKey="rolling_3m" stroke="#4C9EEB" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
          <Line type="monotone" dataKey="rolling_6m" stroke="#A78BFA" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
          <Line type="monotone" dataKey="rolling_12m" stroke="#F0B429" strokeWidth={1.5} dot={false} connectNulls={false} isAnimationActive animationDuration={1400} />
        </LineChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

/* ================================================================== */
/*  Section 2.4: 收益分布 Charts                                      */
/* ================================================================== */

function DistributionHistogram({
  distribution,
  normalMean,
  normalStd,
}: {
  distribution?: DistributionBin[];
  normalMean?: number | null;
  normalStd?: number | null;
}) {
  const { barData, normalCurve } = useMemo(() => {
    if (!distribution?.length) return { barData: [], normalCurve: [] };

    const barData = distribution.map((b) => ({
      mid: parseFloat(((b.bin_start + b.bin_end) / 2).toFixed(4)),
      count: b.count,
    }));

    // Generate normal distribution overlay
    let normalCurve: Array<{ mid: number; normal: number }> = [];
    if (normalMean != null && normalStd != null && normalStd > 0) {
      const totalCount = distribution.reduce((sum, b) => sum + b.count, 0);
      const binWidth = distribution.length > 1
        ? distribution[1].bin_start - distribution[0].bin_start
        : 1;
      const minX = distribution[0].bin_start;
      const maxX = distribution[distribution.length - 1].bin_end;
      const numPoints = 100;
      const step = (maxX - minX) / numPoints;
      const sigma = normalStd;
      const mu = normalMean;
      const coeff = (1 / (sigma * Math.sqrt(2 * Math.PI))) * totalCount * binWidth;

      normalCurve = Array.from({ length: numPoints + 1 }, (_, i) => {
        const x = minX + i * step;
        const exponent = -0.5 * Math.pow((x - mu) / sigma, 2);
        return {
          mid: parseFloat(x.toFixed(4)),
          normal: coeff * Math.exp(exponent),
        };
      });
    }

    return { barData, normalCurve };
  }, [distribution, normalMean, normalStd]);

  // Merge bar data and normal curve for ComposedChart
  // Must be before conditional return to satisfy React hooks rules
  const mergedData = useMemo(() => {
    if (!barData.length) return [];
    const normalMap = new Map(normalCurve.map((n) => [n.mid, n.normal]));
    // Add bar data points
    const result = barData.map((b) => ({
      mid: b.mid,
      count: b.count,
      normal: normalMap.get(b.mid) ?? null,
    }));
    // Add normal-only points between bars for smooth curve
    for (const n of normalCurve) {
      if (!barData.some((b) => Math.abs(b.mid - n.mid) < 0.0001)) {
        result.push({ mid: n.mid, count: 0, normal: n.normal });
      }
    }
    result.sort((a, b) => a.mid - b.mid);
    return result;
  }, [barData, normalCurve]);

  if (!barData.length) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          日收益分布
        </span>
        <ChartPlaceholder />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          日收益分布
        </span>
        {normalCurve.length > 0 && (
          <div className="flex items-center gap-3 text-[9px] text-muted-foreground/50">
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-[2px] rounded bg-[#4C9EEB]" />
              实际
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-3 h-[1px] rounded" style={{ borderTop: "1px dashed #A78BFA" }} />
              正态拟合
            </span>
          </div>
        )}
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart
          data={mergedData}
          margin={{ top: 8, right: 8, left: 8, bottom: 4 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="mid"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => `${Number(v).toFixed(1)}%`}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            width={32}
          />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: unknown) => {
              const v = Number(value);
              if (name === "normal") {
                return [
                  <span key="v" style={{ color: "#A78BFA" }}>{v.toFixed(1)}</span>,
                  "正态拟合",
                ];
              }
              return [
                <span key="v" style={{ color: "#4C9EEB" }}>{v} 天</span>,
                "实际频次",
              ];
            }}
          />
          <ReferenceLine x={0} stroke="rgba(255,255,255,0.2)" strokeWidth={1} />
          <Bar dataKey="count" radius={[2, 2, 0, 0]}>
            {mergedData.map((entry, index) => (
              <Cell
                key={`dist-${index}`}
                fill={entry.mid >= 0 ? "#26D97F" : "#EF5350"}
                fillOpacity={0.75}
              />
            ))}
          </Bar>
          {normalCurve.length > 0 && (
            <Line
              type="monotone"
              dataKey="normal"
              stroke="#A78BFA"
              strokeWidth={1.5}
              strokeDasharray="4 2"
              dot={false}
              connectNulls
              isAnimationActive
              animationDuration={1600}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

function QQPlotChart({ qqData }: { qqData?: QQPlotPoint[] }) {
  const { data, refMin, refMax } = useMemo(() => {
    if (!qqData?.length) return { data: [], refMin: -3, refMax: 3 };
    const theoreticals = qqData.map((p) => p.theoretical);
    const empiricals = qqData.map((p) => p.empirical);
    const refMin = Math.min(...theoreticals, ...empiricals);
    const refMax = Math.max(...theoreticals, ...empiricals);
    return {
      data: qqData.map((p) => ({
        theoretical: p.theoretical,
        empirical: p.empirical,
      })),
      refMin,
      refMax,
    };
  }, [qqData]);

  if (!data.length) {
    return (
      <GlassCard className="p-4">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-2">
          Q-Q 正态图
        </span>
        <ChartPlaceholder />
      </GlassCard>
    );
  }

  return (
    <GlassCard className="p-4 flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          Q-Q 正态图
        </span>
        <span className="text-[9px] text-muted-foreground/30">
          偏离对角线 = 非正态
        </span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <ScatterChart margin={{ top: 8, right: 16, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            type="number"
            dataKey="theoretical"
            name="理论分位数"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => Number(v).toFixed(1)}
            domain={[refMin * 1.1, refMax * 1.1]}
            label={{
              value: "理论分位数 (正态)",
              position: "insideBottom",
              offset: -2,
              fill: "rgba(255,255,255,0.2)",
              fontSize: 8,
            }}
          />
          <YAxis
            type="number"
            dataKey="empirical"
            name="实际分位数"
            tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => Number(v).toFixed(3)}
            width={52}
            label={{
              value: "实际日收益",
              angle: -90,
              position: "insideLeft",
              fill: "rgba(255,255,255,0.2)",
              fontSize: 8,
            }}
          />
          <RechartsTooltip
            contentStyle={tooltipStyle} labelStyle={tooltipLabelStyle} itemStyle={tooltipItemStyle}
            formatter={(value: unknown, name: string | undefined) => [
              Number(value).toFixed(4),
              name ?? "",
            ]}
          />
          <ReferenceLine
            segment={[
              { x: refMin, y: refMin },
              { x: refMax, y: refMax },
            ]}
            stroke="#F0B429"
            strokeDasharray="5 3"
            strokeWidth={1}
            strokeOpacity={0.5}
          />
          <Scatter data={data} fill="#4C9EEB" fillOpacity={0.6} r={2} />
        </ScatterChart>
      </ResponsiveContainer>
    </GlassCard>
  );
}

/* ================================================================== */
/*  Section 2.5: Performance 指标汇总                                  */
/* ================================================================== */

interface MetricRow {
  label: string;
  tooltip: string;
  value: string;
  color?: string;
}

function MetricsSummaryTable({
  statistics: s,
  drawdownPeriods,
  benchmarkType,
}: {
  statistics: BacktestResult["statistics"];
  drawdownPeriods?: DrawdownPeriod[];
  benchmarkType?: string;
}) {
  const worstDD = drawdownPeriods?.[0];

  const categories: { title: string; rows: MetricRow[]; hidden?: boolean }[] = [
    {
      title: "风险调整收益",
      rows: [
        {
          label: "Sharpe Ratio",
          tooltip: "年化超额收益 / 年化波动率。>1 为良好，>2 为优秀",
          value: fmtNum(s.sharpe_ratio),
          color: s.sharpe_ratio != null ? (s.sharpe_ratio >= 1 ? "#26D97F" : s.sharpe_ratio >= 0 ? "#E8EAED" : "#EF5350") : undefined,
        },
        {
          label: "Sortino Ratio",
          tooltip: "年化超额收益 / 下行波动率。只惩罚负收益，比 Sharpe 更公平",
          value: fmtNum(s.sortino_ratio),
          color: s.sortino_ratio != null ? (s.sortino_ratio >= 1 ? "#26D97F" : s.sortino_ratio >= 0 ? "#E8EAED" : "#EF5350") : undefined,
        },
        {
          label: "Calmar Ratio",
          tooltip: "年化收益 / 最大回撤。衡量收益相对于最坏情况的能力",
          value: fmtNum(s.calmar_ratio),
          color: s.calmar_ratio != null ? (s.calmar_ratio >= 1 ? "#26D97F" : s.calmar_ratio >= 0 ? "#E8EAED" : "#EF5350") : undefined,
        },
        {
          label: "Omega Ratio",
          tooltip: "收益概率加权的积极/消极比。>1 表示收益分布偏向盈利",
          value: fmtNum(s.omega_ratio),
          color: s.omega_ratio != null ? (s.omega_ratio >= 1 ? "#26D97F" : "#EF5350") : undefined,
        },
      ],
    },
    {
      title: "回撤与风险",
      rows: [
        {
          label: "最大回撤",
          tooltip: "峰值到谷底的最大跌幅百分比",
          value: s.max_drawdown != null ? `${s.max_drawdown.toFixed(2)}%` : "N/A",
          color: "#EF5350",
        },
        {
          label: "最大回撤持续",
          tooltip: "最严重回撤从开始到谷底经历的天数",
          value: worstDD ? `${worstDD.duration_days} 天` : "N/A",
        },
        {
          label: "恢复时间",
          tooltip: "最严重回撤从谷底恢复到新高的天数",
          value: worstDD?.recovery_days != null ? `${worstDD.recovery_days} 天` : "未恢复",
          color: worstDD?.recovery_days == null ? "#EF5350" : undefined,
        },
        {
          label: "VaR (95%)",
          tooltip: "95% 置信度下单日最大预期亏损",
          value: s.var_95 != null ? `${s.var_95.toFixed(2)}%` : "N/A",
          color: "#EF5350",
        },
        {
          label: "VaR (99%)",
          tooltip: "99% 置信度下单日最大预期亏损",
          value: s.var_99 != null ? `${s.var_99.toFixed(2)}%` : "N/A",
          color: "#EF5350",
        },
        {
          label: "CVaR (95%)",
          tooltip: "条件在险价值，超过 VaR 时的平均损失",
          value: s.cvar_95 != null ? `${s.cvar_95.toFixed(2)}%` : "N/A",
          color: "#EF5350",
        },
        {
          label: "下行偏差",
          tooltip: "仅计算负收益的标准差，衡量下行风险",
          value: s.downside_deviation != null ? `${(s.downside_deviation * 100).toFixed(2)}%` : "N/A",
        },
        {
          label: "Ulcer Index",
          tooltip: "基于回撤深度和持续时间的风险指标，越低越好",
          value: fmtNum(s.ulcer_index, 4),
        },
      ],
    },
    {
      title: "分布特征",
      rows: [
        {
          label: "偏度 (Skewness)",
          tooltip: "收益分布的不对称性。正偏=右尾更长，负偏=左尾更长",
          value: fmtNum(s.skewness, 3),
          color: s.skewness != null ? (s.skewness > 0 ? "#26D97F" : "#EF5350") : undefined,
        },
        {
          label: "峰度 (Kurtosis)",
          tooltip: "超额峰度，衡量尾部厚度。>0 表示极端事件概率高于正态分布",
          value: fmtNum(s.kurtosis, 3),
        },
        {
          label: "尾部比率 (Tail Ratio)",
          tooltip: "95%分位 / |5%分位|，>1 表示上行尾部更厚",
          value: fmtNum(s.tail_ratio, 3),
          color: s.tail_ratio != null ? (s.tail_ratio > 1 ? "#26D97F" : "#EF5350") : undefined,
        },
        {
          label: "稳定性 (R²)",
          tooltip: "累计收益对线性回归的拟合度，越接近 1 越稳定",
          value: fmtNum(s.stability, 4),
          color: s.stability != null ? (s.stability > 0.8 ? "#26D97F" : "#EF5350") : undefined,
        },
        {
          label: "最大单日亏损",
          tooltip: "回测期间单日最大亏损率",
          value: s.max_daily_loss != null ? `${s.max_daily_loss.toFixed(2)}%` : "N/A",
          color: "#EF5350",
        },
      ],
    },
    {
      title: "基准相对指标",
      hidden: benchmarkType === "zero_line",
      rows: [
        {
          label: "Alpha",
          tooltip: "超越基准的年化超额收益",
          value: s.alpha != null ? `${(s.alpha * 100).toFixed(2)}%` : "N/A",
          color: s.alpha != null ? (s.alpha > 0 ? "#26D97F" : "#EF5350") : undefined,
        },
        {
          label: "Beta",
          tooltip: "相对基准的系统性风险敞口。1=同步，<1=防御，>1=激进",
          value: fmtNum(s.beta, 3),
        },
        {
          label: "R²",
          tooltip: "策略收益被基准解释的比例。越高表示越像基准",
          value: fmtNum(s.r_squared, 4),
        },
        {
          label: "Information Ratio",
          tooltip: "超额收益 / 跟踪误差。衡量主动管理的效率",
          value: fmtNum(s.information_ratio, 3),
          color: s.information_ratio != null ? (s.information_ratio > 0 ? "#26D97F" : "#EF5350") : undefined,
        },
      ],
    },
  ];

  return (
    <GlassCard className="p-5">
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 block mb-4">
        Performance 指标汇总
      </span>
      <div className="grid grid-cols-2 gap-6">
        {categories
          .filter((cat) => !cat.hidden)
          .map((cat) => (
            <div key={cat.title}>
              <span className="text-[9px] font-semibold tracking-[1px] uppercase text-[var(--accent-blue)] opacity-70 block mb-2">
                {cat.title}
              </span>
              <div className="flex flex-col">
                {cat.rows.map((row) => (
                  <div
                    key={row.label}
                    className="flex items-center justify-between py-1.5 border-b border-white/[0.04] last:border-0"
                  >
                    <span className="text-[10px] text-muted-foreground/60 inline-flex items-center">
                      {row.label}
                      <HelpTip text={row.tooltip} />
                    </span>
                    <span
                      className="text-[11px] font-medium font-heading"
                      style={{ color: row.color ?? "#E8EAED" }}
                    >
                      {row.value}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
      </div>
    </GlassCard>
  );
}

/* ================================================================== */
/*  Loading Skeleton                                                   */
/* ================================================================== */

function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-6 p-5">
      <Skeleton className="h-4 w-32 rounded" />
      <div className="grid grid-cols-2 gap-3">
        <Skeleton className="h-72 rounded-xl" />
        <Skeleton className="h-72 rounded-xl" />
      </div>
      <Skeleton className="h-44 rounded-xl" />
      <div className="grid grid-cols-5 gap-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <Skeleton className="h-4 w-32 rounded" />
      <Skeleton className="h-48 rounded-xl" />
      <div className="grid grid-cols-2 gap-3">
        <Skeleton className="h-48 rounded-xl" />
        <Skeleton className="h-48 rounded-xl" />
      </div>
    </div>
  );
}

/* ================================================================== */
/*  Main Component                                                     */
/* ================================================================== */

export function PerformanceTab({ runId }: { runId: string }) {
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
        if (!cancelled) setResult(data as BacktestResult);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) return <LoadingSkeleton />;

  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-[var(--accent-red)]">
          {error ?? "加载失败"}
        </span>
      </div>
    );
  }

  const {
    statistics: s,
    equity_curve,
    annual_returns,
    rolling_returns,
    returns_distribution,
    qq_plot_data,
    benchmark_equity_curve,
    monthly_returns,
    weekly_returns,
    drawdown_periods,
    rolling_sharpe,
    rolling_sortino,
    rolling_volatility,
    rolling_beta,
    benchmark_type,
  } = result;

  // Derive starting balance from first equity point
  const startingBalance =
    equity_curve?.length > 0
      ? equity_curve[0].equity
      : (() => {
          const finalEq = s.final_balance
            ? parseFloat(s.final_balance.split(" ")[0])
            : null;
          return finalEq != null ? finalEq - s.total_pnl : 10000;
        })();

  // Parse annual_return from statistics: comes as fraction (0.xx), convert to %
  const annualReturnPct =
    s.annual_return != null ? s.annual_return * 100 : null;

  const totalReturnPct = s.total_return_pct ?? 0;

  const volatilityPct =
    s.returns_volatility != null ? s.returns_volatility * 100 : null;

  const showBenchmarkRelative = benchmark_type !== "zero_line";

  return (
    <div className="flex flex-col gap-6 p-5">
      {/* ============================================================ */}
      {/* Section 2.1: 权益表现                                        */}
      {/* ============================================================ */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col gap-4"
      >
        <SectionHeader title="权益表现" index={0} />

        {/* Enhanced equity curve — full width */}
        <EnhancedEquityCurve
          equityCurve={equity_curve}
          benchmarkCurve={benchmark_equity_curve}
          startingBalance={startingBalance}
        />

        {/* Drawdown chart */}
        <DrawdownChart equityCurve={equity_curve} />

        {/* Top 5 Drawdowns table */}
        <Top5DrawdownsTable drawdownPeriods={drawdown_periods} />

        {/* 5 metric cards */}
        <div className="grid grid-cols-5 gap-3">
          <MetricCard
            label="总收益率"
            tooltip="策略从开始到结束的累计百分比收益"
            value={totalReturnPct}
            suffix="%"
            showSign
            positive={totalReturnPct >= 0}
            index={0}
          />
          <MetricCard
            label="年化收益 CAGR"
            sublabel="复利年化增长率"
            tooltip="按复利计算的年化增长率，便于跨周期比较"
            value={annualReturnPct}
            suffix="%"
            showSign
            positive={annualReturnPct != null ? annualReturnPct >= 0 : null}
            index={1}
          />
          <MetricCard
            label="年化波动率"
            sublabel="日收益标准差 x sqrt(365)"
            tooltip="日收益率标准差 x sqrt(365)，衡量收益的波动程度"
            value={volatilityPct}
            suffix="%"
            positive={null}
            index={2}
          />
          <MetricCard
            label="最佳单日"
            tooltip="回测期间单日最高收益率"
            value={s.best_day ?? null}
            suffix="%"
            showSign
            positive={true}
            index={3}
          />
          <MetricCard
            label="最差单日"
            tooltip="回测期间单日最大亏损率"
            value={s.worst_day ?? null}
            suffix="%"
            showSign
            positive={false}
            index={4}
          />
        </div>
      </motion.div>

      {/* ============================================================ */}
      {/* Section 2.2: 周期收益                                        */}
      {/* ============================================================ */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.12, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col gap-4"
      >
        <SectionHeader title="周期收益" index={1} />

        {/* Monthly heatmap — full width */}
        <MonthlyHeatmap monthlyReturns={monthly_returns} />

        {/* Annual + Weekly in 2-col */}
        <div className="grid grid-cols-2 gap-3">
          <AnnualReturnsChart annualReturns={annual_returns} />
          <WeeklyReturnsChart weeklyReturns={weekly_returns} />
        </div>

        {/* 3 metric cards */}
        <div className="grid grid-cols-3 gap-3">
          <MetricCard
            label="最佳单月"
            tooltip="回测期间单月最高收益率"
            value={s.best_month ?? null}
            suffix="%"
            showSign
            positive={true}
            index={0}
          />
          <MetricCard
            label="最差单月"
            tooltip="回测期间单月最大亏损率"
            value={s.worst_month ?? null}
            suffix="%"
            showSign
            positive={false}
            index={1}
          />
          <MetricCard
            label="盈利月份"
            sublabel="月度收益为正的比例"
            tooltip="月度收益为正的月份占总月份的比例"
            value={s.positive_months_pct ?? null}
            suffix="%"
            decimals={1}
            positive={
              s.positive_months_pct != null ? s.positive_months_pct >= 50 : null
            }
            index={2}
          />
        </div>
      </motion.div>

      {/* ============================================================ */}
      {/* Section 2.3: 滚动分析                                        */}
      {/* ============================================================ */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.24, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col gap-4"
      >
        <SectionHeader title="滚动分析" index={2} />

        <div className="grid grid-cols-2 gap-3">
          <RollingSharpeChart data={rolling_sharpe} />
          <RollingSortinoChart data={rolling_sortino} />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <RollingVolatilityChart data={rolling_volatility} />
          {showBenchmarkRelative && <RollingBetaChart data={rolling_beta} />}
        </div>

        {/* Rolling returns — full width */}
        <RollingReturnsChart rollingReturns={rolling_returns} />
      </motion.div>

      {/* ============================================================ */}
      {/* Section 2.4: 收益分布                                        */}
      {/* ============================================================ */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.36, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col gap-4"
      >
        <SectionHeader title="收益分布" index={3} />

        <div className="grid grid-cols-2 gap-3">
          <DistributionHistogram
            distribution={returns_distribution}
            normalMean={s.normal_dist_mean}
            normalStd={s.normal_dist_std}
          />
          <QQPlotChart qqData={qq_plot_data} />
        </div>
      </motion.div>

      {/* ============================================================ */}
      {/* Section 2.5: Performance 指标汇总                             */}
      {/* ============================================================ */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45, delay: 0.48, ease: [0.22, 1, 0.36, 1] }}
        className="flex flex-col gap-4"
      >
        <SectionHeader title="Performance 指标汇总" index={4} />

        <MetricsSummaryTable
          statistics={s}
          drawdownPeriods={drawdown_periods}
          benchmarkType={benchmark_type}
        />
      </motion.div>
    </div>
  );
}
