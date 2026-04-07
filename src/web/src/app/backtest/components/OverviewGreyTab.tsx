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
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
} from "recharts";
import { HelpCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { API_BASE } from "@/lib/api";
import { useCountUp } from "@/hooks/useCountUp";
import { CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import type { BacktestResult } from "../types";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

/* ------------------------------------------------------------------ */
/*  Help Tooltip — small ? icon with explanation                       */
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

/* ------------------------------------------------------------------ */
/*  Glass Card wrapper                                                 */
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
      className={`rounded-xl border bg-card overflow-hidden ${className}`}
    >
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Hero Banner — Total Return + Final Equity + mini sparkline         */
/* ------------------------------------------------------------------ */

function HeroBanner({
  totalReturn,
  finalEquity,
  totalPnl,
  startingBalance,
  totalFees,
}: {
  totalReturn: number;
  finalEquity: number | null;
  totalPnl: number;
  startingBalance: number;
  totalFees: number;
}) {
  const isPositive = totalReturn >= 0;
  const returnAnimated = useCountUp(totalReturn, 1200, true);
  const equityAnimated = useCountUp(finalEquity ?? 0, 1200, finalEquity !== null);
  const pnlAnimated = useCountUp(totalPnl, 1000, true);

  return (
    <div
      className="overview-grey-hero relative rounded-xl border bg-card p-6 overflow-hidden"
    >
      {/* Gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-br from-white/[0.03] via-transparent to-transparent pointer-events-none" />

      <div className="relative flex items-end justify-between">
        {/* Left: Total Return */}
        <div className="flex flex-col gap-1.5">
          <span className="qds-section-label inline-flex items-center">
            总收益率
            <HelpTip text="回测期间的总投资回报百分比，即 (最终权益 - 初始资金) / 初始资金" />
          </span>
          <div className="flex items-baseline gap-3">
            <span
              className={`text-5xl font-black font-mono tracking-tight leading-none ${
                isPositive
                  ? "text-qds-success"
                  : "text-destructive"
              }`}
              style={{
                textShadow: isPositive
                  ? "0 0 40px rgba(38, 217, 127, 0.25), 0 0 80px rgba(38, 217, 127, 0.1)"
                  : "0 0 40px rgba(239, 83, 80, 0.25), 0 0 80px rgba(239, 83, 80, 0.1)",
              }}
            >
              {returnAnimated >= 0 ? "+" : ""}
              {returnAnimated.toFixed(2)}%
            </span>
          </div>
          {/* PnL + meta info */}
          <span
            className={`text-sm font-medium ${
              isPositive
                ? "text-qds-success/70"
                : "text-destructive/70"
            }`}
          >
            {pnlAnimated >= 0 ? "+" : ""}
            {pnlAnimated.toLocaleString("en-US", {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}{" "}
            USDT
          </span>
          <div className="flex items-center gap-4 mt-1">
            <span className="text-xs text-muted-foreground">
              初始资金 <span className="font-semibold text-foreground">${startingBalance.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
            </span>
            <span className="text-qds-t3">·</span>
            <span className="text-xs text-muted-foreground">
              手续费 <span className="font-semibold text-foreground">${totalFees.toLocaleString("en-US", { maximumFractionDigits: 2 })}</span>
            </span>
          </div>
        </div>

        {/* Right: Final Equity */}
        {finalEquity !== null && (
          <div className="flex flex-col items-end gap-1.5">
            <span className="qds-section-label inline-flex items-center">
              最终权益
              <HelpTip text="回测结束时的账户总价值，包含初始资金和所有已实现盈亏" />
            </span>
            <span className="text-3xl font-bold font-mono text-foreground tracking-tight">
              $
              {equityAnimated.toLocaleString("en-US", {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
              })}
            </span>
          </div>
        )}
      </div>

    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Metric Card — animated KPI with accent line                        */
/* ------------------------------------------------------------------ */

interface MetricCardProps {
  label: string;
  sublabel?: string;
  tooltip?: string;
  value: number | null;
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
  const animated = useCountUp(numeric, 800 + index * 80, value !== null);

  const colorClass =
    positive === null || positive === undefined
      ? "text-foreground"
      : positive
        ? "text-qds-success"
        : "text-destructive";

  const accentColor =
    positive === null || positive === undefined
      ? "rgba(76, 158, 235, 0.5)"
      : positive
        ? "rgba(38, 217, 127, 0.5)"
        : "rgba(239, 83, 80, 0.5)";

  const formatted =
    value === null
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
      {/* Bottom accent glow line */}
      <div
        className="absolute bottom-0 left-0 right-0 h-[2px] opacity-30 group-hover:opacity-70 transition-opacity duration-500"
        style={{
          background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`,
        }}
      />

      <span className="qds-section-label inline-flex items-center">
        {label}
        {tooltip && <HelpTip text={tooltip} />}
      </span>
      <span
        className={`text-2xl font-bold font-mono tracking-tight leading-none ${colorClass}`}
      >
        {formatted}
      </span>
      {sublabel && (
        <span className="text-[9px] text-qds-t3 leading-tight">
          {sublabel}
        </span>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Performance Radar — spider chart of normalized metrics             */
/* ------------------------------------------------------------------ */

function PerformanceRadar({ s }: { s: BacktestResult["statistics"] }) {
  const data = useMemo(() => {
    // 年化收益率 (CAGR): 100% = 满分
    const cagrNorm = clamp((s.annual_return ?? 0), 0, 1) * 100;
    // 夏普比率: 2.5 = 满分
    const sharpeNorm = clamp((s.sharpe_ratio ?? 0) / 2.5, 0, 1) * 100;
    // 胜率: 直接百分比
    const winRateNorm = (s.win_rate ?? 0) * 100;
    // 盈亏比: 3.0 = 满分
    const pfNorm = clamp(((s.profit_factor ?? 0.5) - 0.5) / 2.5, 0, 1) * 100;
    // 抗回撤: 0% 回撤 = 满分
    const ddResistNorm = clamp(1 - Math.abs(s.max_drawdown ?? 0), 0, 1) * 100;
    // 稳定性 (Sortino): 3.0 = 满分 (数据来自权益曲线日收益下行标准差)
    const sortinoNorm = clamp((s.sortino_ratio ?? 0) / 3, 0, 1) * 100;

    return [
      { metric: "年化", value: cagrNorm, fullMark: 100 },
      { metric: "夏普", value: sharpeNorm, fullMark: 100 },
      { metric: "胜率", value: winRateNorm, fullMark: 100 },
      { metric: "盈亏比", value: pfNorm, fullMark: 100 },
      { metric: "抗回撤", value: ddResistNorm, fullMark: 100 },
      { metric: "稳定性", value: sortinoNorm, fullMark: 100 },
    ];
  }, [s]);

  return (
    <div
    >
      <GlassCard className="p-4 h-full flex flex-col">
        <span className="qds-section-label inline-flex items-center">
          绩效画像
          <HelpTip text="将多个关键指标归一化到 0-100 分制，直观展示策略的综合表现维度" />
        </span>
        <div className="flex-1 min-h-0">
          <ResponsiveContainer width="100%" height={240}>
            <RadarChart data={data} cx="50%" cy="50%" outerRadius="72%">
              <defs>
                <linearGradient id="radarFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#4C9EEB" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="#A78BFA" stopOpacity={0.08} />
                </linearGradient>
              </defs>
              <PolarGrid
                stroke="rgba(255,255,255,0.06)"
                strokeDasharray="2 4"
              />
              <PolarAngleAxis
                dataKey="metric"
                tick={{
                  fill: "rgba(255,255,255,0.45)",
                  fontSize: 10,
                  fontWeight: 500,
                }}
                tickLine={false}
              />
              <Radar
                name="Performance"
                dataKey="value"
                stroke="#4C9EEB"
                fill="url(#radarFill)"
                strokeWidth={2}
                dot={{
                  r: 3,
                  fill: "#4C9EEB",
                  stroke: "rgba(76, 158, 235, 0.4)",
                  strokeWidth: 4,
                }}
                isAnimationActive={true}
                animationDuration={1200}
                animationEasing="ease-out"
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </GlassCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Equity Curve + Drawdown Chart                                      */
/* ------------------------------------------------------------------ */

function EquityDrawdownChart({
  equityCurve,
  startingBalance,
}: {
  equityCurve: BacktestResult["equity_curve"];
  startingBalance: number;
}) {
  const chartData = useMemo(() => {
    const maxPoints = 220;
    const step =
      equityCurve.length > maxPoints
        ? Math.ceil(equityCurve.length / maxPoints)
        : 1;
    return equityCurve
      .filter((_, i) => i % step === 0)
      .map((p) => ({
        t: new Date(p.timestamp ?? p.date ?? "").toLocaleDateString("zh-CN", {
          month: "short",
          day: "numeric",
        }),
        equity: p.equity,
        drawdown: p.drawdown_pct ?? 0,
      }));
  }, [equityCurve]);

  if (chartData.length < 2) return null;

  // Compute gradient stop position: where startingBalance falls in value range
  // SVG gradient y1=0 is top (max equity), y2=1 is bottom (min equity)
  const equities = chartData.map((d) => d.equity);
  const minEq = Math.min(...equities);
  const maxEq = Math.max(...equities);
  const range = maxEq - minEq || 1;
  // Clamp to [0.01, 0.99] so both colors always have some presence
  const balanceStop = clamp((maxEq - startingBalance) / range, 0.01, 0.99);


  return (
    <div
    >
      <GlassCard className="p-4 flex flex-col gap-3">
        <span className="qds-section-label">
          权益曲线
        </span>
        <ResponsiveContainer width="100%" height={210}>
          <AreaChart
            data={chartData}
            margin={{ top: 4, right: 8, left: 8, bottom: 4 }}
          >
            <defs>
              {/* Vertical color gradient: blue above starting balance, red below */}
              <linearGradient id="greyEqStroke" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#4C9EEB" />
                <stop offset={balanceStop} stopColor="#4C9EEB" />
                <stop offset={balanceStop} stopColor="#EF5350" />
                <stop offset="100%" stopColor="#EF5350" />
              </linearGradient>
              <filter id="eqLineGlow" x="-20%" y="-20%" width="140%" height="140%">
                <feGaussianBlur in="SourceGraphic" stdDeviation="4" />
              </filter>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="rgba(255,255,255,0.04)"
            />
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
              tickFormatter={(v) =>
                `$${(Number(v) / 1000).toFixed(Number(v) >= 10000 ? 0 : 1)}k`
              }
              width={48}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown, _name: unknown, props: { payload?: { equity?: number } }) => {
                const eq = props?.payload?.equity ?? Number(value);
                const color = eq >= startingBalance ? "#4C9EEB" : "#EF5350";
                return [
                  <span key="v" style={{ color }}>${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>,
                  "权益",
                ];
              }}
            />
            <ReferenceLine
              y={startingBalance}
              stroke="#F0B429"
              strokeDasharray="6 4"
              strokeWidth={1}
              strokeOpacity={0.4}
            />
            {/* Glow layer — hidden from tooltip */}
            <Area
              type="monotone"
              dataKey="equity"
              stroke="url(#greyEqStroke)"
              strokeWidth={5}
              fill="none"
              dot={false}
              filter="url(#eqLineGlow)"
              opacity={0.25}
              isAnimationActive={true}
              animationDuration={1800}
              tooltipType="none"
            />
            {/* Main line */}
            <Area
              type="monotone"
              dataKey="equity"
              stroke="url(#greyEqStroke)"
              strokeWidth={1.5}
              fill="none"
              dot={false}
              activeDot={{
                r: 4,
                fill: "#4C9EEB",
                stroke: "rgba(76, 158, 235, 0.3)",
                strokeWidth: 6,
              }}
              isAnimationActive={true}
              animationDuration={1800}
              animationEasing="ease-out"
            />
          </AreaChart>
        </ResponsiveContainer>

        {/* Drawdown underwater chart */}
        <span className="qds-section-label mt-1">
          回撤曲线
        </span>
        <ResponsiveContainer width="100%" height={80}>
          <AreaChart
            data={chartData}
            margin={{ top: 2, right: 8, left: 8, bottom: 2 }}
          >
            <defs>
              <linearGradient id="greyDdGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#EF5350" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#EF5350" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="rgba(255,255,255,0.04)"
            />
            <XAxis dataKey="t" hide />
            <YAxis
              tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
              tickLine={false}
              axisLine={false}
              tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
              width={48}
            />
            <RechartsTooltip
              {...CHART_TOOLTIP_PROPS}
              formatter={(value: unknown) => [
                `${Number(value).toFixed(2)}%`,
                "回撤",
              ]}
            />
            <Area
              type="monotone"
              dataKey="drawdown"
              stroke="#EF5350"
              strokeWidth={1}
              fill="url(#greyDdGrad)"
              dot={false}
              isAnimationActive={true}
              animationDuration={1800}
              animationEasing="ease-out"
            />
          </AreaChart>
        </ResponsiveContainer>
      </GlassCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

interface OverviewGreyTabProps {
  runId: string;
}

export function OverviewGreyTab({ runId }: OverviewGreyTabProps) {
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

    return () => {
      cancelled = true;
    };
  }, [runId]);

  /* Loading skeleton */
  if (loading) {
    return (
      <div className="flex flex-col gap-4 p-5">
        <Skeleton className="h-32 rounded-xl" />
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
        <div className="grid grid-cols-[1fr_280px] gap-3">
          <Skeleton className="h-72 rounded-xl" />
          <Skeleton className="h-72 rounded-xl" />
        </div>
      </div>
    );
  }

  /* Error state */
  if (error || !result) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-destructive">
          {error ?? "加载失败"}
        </span>
      </div>
    );
  }

  const { statistics: s, equity_curve } = result;

  // Parse final balance (e.g. "10114.60 USDT" → 10114.60)
  const finalEquity = s.final_balance
    ? parseFloat(s.final_balance.split(" ")[0])
    : null;

  // Starting balance = final equity - total pnl
  const startingBalance =
    finalEquity !== null ? finalEquity - s.total_pnl : 10000;


  return (
    <div className="flex flex-col gap-4 p-5">
      {/* 1. Hero Banner — Total Return + Final Equity */}
      <HeroBanner
        totalReturn={s.total_return_pct}
        finalEquity={finalEquity}
        totalPnl={s.total_pnl}
        startingBalance={startingBalance}
        totalFees={s.total_fees}
      />

      {/* 2. KPI 指标卡片 — 3×2 网格 */}
      <div className="grid grid-cols-3 gap-3">
        <MetricCard
          label="年化收益率"
          sublabel="复合年化增长率 (365天)"
          tooltip="将总收益折算为每年的等效复合增长率，用于衡量策略的长期盈利能力"
          value={s.annual_return !== null ? s.annual_return * 100 : null}
          suffix="%"
          showSign
          positive={s.annual_return !== null ? s.annual_return >= 0 : null}
          index={0}
        />
        <MetricCard
          label="夏普比率"
          sublabel="风险调整后收益"
          tooltip="每承受一单位波动风险所获得的超额收益，> 1 为佳，> 2 为优秀"
          value={s.sharpe_ratio}
          positive={s.sharpe_ratio !== null ? s.sharpe_ratio >= 1 : null}
          index={1}
        />
        <MetricCard
          label="最大回撤"
          sublabel="峰值到谷底"
          tooltip="权益从历史最高点到最低点的最大跌幅，衡量最坏情况下可能承受的损失"
          value={s.max_drawdown !== null ? Math.abs(s.max_drawdown) * 100 : null}
          prefix="-"
          suffix="%"
          positive={s.max_drawdown !== null ? Math.abs(s.max_drawdown) < 0.2 : null}
          index={2}
        />
        <MetricCard
          label="胜率"
          sublabel={`${s.winning_trades} 盈 / ${s.losing_trades} 亏`}
          tooltip="盈利交易笔数占总交易笔数的比例，配合盈亏比综合评估策略质量"
          value={s.win_rate * 100}
          decimals={1}
          suffix="%"
          positive={s.win_rate >= 0.5}
          index={3}
        />
        <MetricCard
          label="盈亏比"
          sublabel="总盈利 ÷ 总亏损"
          tooltip="总盈利金额与总亏损金额的比值，> 1 表示策略整体盈利，越高越好"
          value={s.profit_factor}
          positive={s.profit_factor !== null ? s.profit_factor >= 1 : null}
          index={4}
        />
        <MetricCard
          label="总交易"
          sublabel={`${s.winning_trades} 盈 / ${s.losing_trades} 亏`}
          tooltip="已平仓的完整交易笔数（一次开仓到平仓算一笔）。每笔交易可能包含多个订单（开仓、平仓、止损等），因此总订单数通常大于交易数"
          value={s.total_trades}
          decimals={0}
          positive={null}
          index={5}
        />
      </div>

      {/* 3. Charts row — Equity+Drawdown (left) + Radar (right) */}
      <div className="grid grid-cols-[1fr_280px] gap-3">
        <EquityDrawdownChart
          equityCurve={equity_curve}
          startingBalance={startingBalance}
        />
        <PerformanceRadar s={s} />
      </div>
    </div>
  );
}
