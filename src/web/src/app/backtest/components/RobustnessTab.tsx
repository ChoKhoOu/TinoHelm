"use client";

import { useMemo, useState, useEffect } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  CartesianGrid,
  BarChart,
  Bar,
  LineChart,
  Line,
  ReferenceLine,
} from "recharts";
import { HelpCircle, Check, X as XIcon, AlertTriangle } from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { API_BASE } from "@/lib/api";
import { CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import type { BacktestResult, RobustnessMetrics } from "../types";

/* ------------------------------------------------------------------ */
/*  Shared UI Primitives                                               */
/* ------------------------------------------------------------------ */

function GlassCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border bg-card overflow-hidden hover:border-qds-border-hover transition-colors duration-[var(--dur)] ${className}`}>
      {children}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="qds-section-label">
      {children}
    </div>
  );
}

function HelpTip({ text }: { text: string }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger className="inline-flex items-center justify-center ml-1 cursor-help">
          <HelpCircle className="w-3 h-3 text-qds-t3 hover:text-muted-foreground transition-colors" />
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[260px] text-[11px] leading-relaxed">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function MetricRow({ label, value, help, color, suffix = "" }: {
  label: string; value: string | number | null | undefined; help?: string; color?: string; suffix?: string;
}) {
  if (value == null) return null;
  return (
    <div className="flex items-center justify-between py-1.5">
      <div className="flex items-center gap-1">
        <span className="text-[11px] text-qds-t1">{label}</span>
        {help && <HelpTip text={help} />}
      </div>
      <span className="text-[13px] font-bold font-mono" style={{ color: color || "var(--foreground)" }}>
        {typeof value === "number" ? value.toFixed(1) : value}{suffix}
      </span>
    </div>
  );
}

const cardAnim = (delay: number) => ({
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.4, delay, ease: [0.22, 1, 0.36, 1] as const },
});


/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface Props {
  runId: string;
}

/* ------------------------------------------------------------------ */
/*  Section 1: Statistical Tests                                       */
/* ------------------------------------------------------------------ */

function StatisticalTestsSection({ r }: { r: RobustnessMetrics }) {
  const psr = r.psr;
  const mbl = r.min_backtest_length_days;
  const actual = r.actual_backtest_length_days;
  const sufficient = r.backtest_length_sufficient;

  const psrColor = psr != null
    ? psr >= 0.95 ? "var(--suc)"
      : psr >= 0.85 ? "var(--info)"
        : psr >= 0.5 ? "var(--warn)"
          : "var(--dan)"
    : undefined;

  return (
    <div {...cardAnim(0)}>
      <GlassCard className="p-5">
        <SectionLabel>Statistical Tests</SectionLabel>
        <div className="space-y-0">
          <MetricRow
            label="PSR"
            value={psr != null ? `${(psr * 100).toFixed(1)}%` : null}
            help="Probabilistic Sharpe Ratio — 估计的 Sharpe 超过零的概率，校正偏度、峰度、样本量 (Bailey & Lopez de Prado 2012)"
            color={psrColor}
          />
          <MetricRow
            label="最短回测天数"
            value={mbl}
            help="以当前 Sharpe 拒绝零假设所需的最短回测天数 (95% 置信度)"
            suffix=" 天"
          />
          <MetricRow
            label="实际回测天数"
            value={actual}
            suffix=" 天"
          />
          {sufficient != null && (
            <div className="flex items-center justify-between py-1.5">
              <span className="text-[11px] text-qds-t1">样本量充足</span>
              <div className="flex items-center gap-1">
                {sufficient ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-qds-success" />
                    <span className="text-[12px] font-bold text-qds-success">是</span>
                  </>
                ) : (
                  <>
                    <XIcon className="w-3.5 h-3.5 text-destructive" />
                    <span className="text-[12px] font-bold text-destructive">否</span>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </GlassCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Section 2: Monte Carlo Equity Cone                                 */
/* ------------------------------------------------------------------ */

function McEquityConeSection({ r }: { r: RobustnessMetrics }) {
  const cone = r.mc_equity_cone;
  if (!cone) return null;

  const data = useMemo(() => {
    return cone.x_labels.map((x, i) => ({
      x,
      p5: cone.curves.p5[i],
      p25: cone.curves.p25[i],
      p50: cone.curves.p50[i],
      p75: cone.curves.p75[i],
      p95: cone.curves.p95[i],
      original: cone.original[i],
      // For area bands: compute band heights
      band_outer_low: cone.curves.p5[i],
      band_outer: cone.curves.p95[i] - cone.curves.p5[i],
      band_inner_low: cone.curves.p25[i],
      band_inner: cone.curves.p75[i] - cone.curves.p25[i],
    }));
  }, [cone]);

  return (
    <div {...cardAnim(0.07)}>
      <GlassCard className="p-5">
        <SectionLabel>
          Monte Carlo Equity Cone
          <HelpTip text={`随机打乱 ${r.mc_num_simulations ?? 1000} 次交易顺序后的权益曲线分布。阴影区域为 5%/95% 和 25%/75% 置信带。白线为实际结果。`} />
        </SectionLabel>
        <div className="h-[300px]">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
              <defs>
                <linearGradient id="mcOuterGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--info)" stopOpacity={0.08} />
                  <stop offset="100%" stopColor="var(--info)" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="mcInnerGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--info)" stopOpacity={0.15} />
                  <stop offset="100%" stopColor="var(--info)" stopOpacity={0.05} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />
              <XAxis
                dataKey="x"
                tick={{ fontSize: 9, fill: "var(--t2)" }}
                tickLine={false}
                axisLine={{ stroke: "rgba(255,255,255,0.06)" }}
                label={{ value: "交易序号", position: "insideBottom", offset: -2, fontSize: 9, fill: "rgba(255,255,255,0.25)" }}
              />
              <YAxis
                tick={{ fontSize: 9, fill: "var(--t2)" }}
                tickLine={false}
                axisLine={{ stroke: "rgba(255,255,255,0.06)" }}
                tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}k`}
              />
              <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
              {/* 5%-95% outer band */}
              <Area type="monotone" dataKey="p5" stackId="outer" stroke="none" fill="transparent" />
              <Area type="monotone" dataKey="band_outer" stackId="outer" stroke="none" fill="url(#mcOuterGrad)" name="5%-95%" />
              {/* 25%-75% inner band */}
              <Area type="monotone" dataKey="p25" stackId="inner" stroke="none" fill="transparent" />
              <Area type="monotone" dataKey="band_inner" stackId="inner" stroke="none" fill="url(#mcInnerGrad)" name="25%-75%" />
              {/* Median line */}
              <Area type="monotone" dataKey="p50" stroke="var(--info)" strokeWidth={1} strokeDasharray="4 3" fill="none" name="中位数" dot={false} />
              {/* Original equity */}
              <Area type="monotone" dataKey="original" stroke="rgba(255,255,255,0.9)" strokeWidth={1.5} fill="none" name="实际" dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </GlassCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Section 3: MC Summary KPIs                                         */
/* ------------------------------------------------------------------ */

function McSummarySection({ r }: { r: RobustnessMetrics }) {
  if (r.mc_probability_of_loss == null) return null;

  const pLoss = r.mc_probability_of_loss;
  const pLossColor = pLoss <= 5 ? "var(--suc)" : pLoss <= 20 ? "var(--warn)" : "var(--dan)";

  return (
    <div {...cardAnim(0.14)}>
      <GlassCard className="p-5">
        <SectionLabel>Monte Carlo Summary</SectionLabel>
        <div className="flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3">亏损概率</span>
            <span className="text-[13px] font-bold font-mono" style={{ color: pLossColor }}>
              {pLoss.toFixed(1)}%
            </span>
          </div>
          <div className="w-px h-4 bg-secondary" />
          <div className="flex items-center gap-2">
            <span className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3">5% 分位收益</span>
            <span className="text-[13px] font-bold font-mono" style={{
              color: (r.mc_5th_percentile_return ?? 0) >= 0 ? "var(--suc)" : "var(--dan)",
            }}>
              {(r.mc_5th_percentile_return ?? 0) >= 0 ? "+" : ""}{r.mc_5th_percentile_return?.toFixed(1)}%
            </span>
          </div>
          <div className="w-px h-4 bg-secondary" />
          <div className="flex items-center gap-2">
            <span className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3">中位最大回撤</span>
            <span className="text-[13px] font-bold font-mono text-destructive">
              {r.mc_median_max_drawdown?.toFixed(1)}%
            </span>
          </div>
          <div className="w-px h-4 bg-secondary" />
          <div className="flex items-center gap-2">
            <span className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3">中位最终收益</span>
            <span className="text-[13px] font-bold font-mono" style={{
              color: (r.mc_median_final_return ?? 0) >= 0 ? "var(--suc)" : "var(--dan)",
            }}>
              {(r.mc_median_final_return ?? 0) >= 0 ? "+" : ""}{r.mc_median_final_return?.toFixed(1)}%
            </span>
          </div>
        </div>
      </GlassCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Section 4: IS vs OOS Comparison (Layer 2)                          */
/* ------------------------------------------------------------------ */

interface IsOosData {
  train_validation?: {
    statistics?: Record<string, unknown>;
    equity_curve?: Array<{ timestamp?: string; equity: number }>;
  } | null;
  validation?: {
    statistics?: Record<string, unknown>;
    equity_curve?: Array<{ timestamp?: string; equity: number }>;
  } | null;
  train_period?: { start: string; end: string };
  test_period?: { start: string; end: string };
  dsr?: number | null;
  parameter_sensitivity?: ParamSensitivityData | null;
  parameter_stability_score?: number | null;
  walk_forward_results?: Array<{ fold: number; test_value: number; train_start: string; test_start: string; test_end: string }>;
}

function IsOosSection({ opt }: { opt: IsOosData }) {
  const isStat = opt.train_validation?.statistics as Record<string, number | null> | undefined;
  const oosStat = opt.validation?.statistics as Record<string, number | null> | undefined;
  if (!isStat || !oosStat) return null;

  // Metrics comparison
  const metrics = [
    { key: "sharpe_ratio", label: "Sharpe" },
    { key: "total_return_pct", label: "收益率" },
    { key: "max_drawdown", label: "最大回撤" },
    { key: "profit_factor", label: "盈亏比" },
    { key: "win_rate", label: "胜率" },
  ];

  const compData = metrics.map((m) => {
    const isVal = (isStat[m.key] as number) ?? 0;
    const oosVal = (oosStat[m.key] as number) ?? 0;
    const degradation = isVal !== 0 ? ((oosVal - isVal) / Math.abs(isVal)) * 100 : 0;
    return { name: m.label, IS: isVal, OOS: oosVal, degradation: Math.round(degradation) };
  });

  // Equity overlay
  const isEq = opt.train_validation?.equity_curve ?? [];
  const oosEq = opt.validation?.equity_curve ?? [];

  const equityData = useMemo(() => {
    const all: Array<{ idx: number; is?: number; oos?: number }> = [];
    isEq.forEach((pt, i) => all.push({ idx: i, is: pt.equity }));
    oosEq.forEach((pt, i) => all.push({ idx: isEq.length + i, oos: pt.equity }));
    return all;
  }, [isEq, oosEq]);

  return (
    <div {...cardAnim(0.21)}>
      <GlassCard className="p-5">
        <SectionLabel>
          IS vs OOS 对比
          <HelpTip text="样本内（训练期）与样本外（测试期）的权益曲线和核心指标对比。衰减越小，策略过拟合风险越低。" />
          {opt.dsr != null && (
            <span className="ml-3 text-[10px] font-mono text-muted-foreground">
              DSR: {(opt.dsr * 100).toFixed(1)}%
            </span>
          )}
        </SectionLabel>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Equity overlay */}
          {equityData.length > 0 && (
            <div>
              <div className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3 mb-2">
                权益曲线叠加
              </div>
              <div className="h-[200px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />
                    <XAxis dataKey="idx" tick={{ fontSize: 9, fill: "var(--t2)" }} tickLine={false} />
                    <YAxis tick={{ fontSize: 9, fill: "var(--t2)" }} tickLine={false} tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}k`} />
                    <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                    {isEq.length > 0 && (
                      <ReferenceLine x={isEq.length - 1} stroke="var(--warn)" strokeDasharray="4 3" strokeOpacity={0.5} label={{ value: "Split", fontSize: 9, fill: "var(--warn)" }} />
                    )}
                    <Area type="monotone" dataKey="is" stroke="var(--info)" strokeWidth={1.5} fill="none" name="IS" dot={false} connectNulls={false} />
                    <Area type="monotone" dataKey="oos" stroke="var(--info)" strokeWidth={1.5} fill="none" name="OOS" dot={false} connectNulls={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Metrics comparison */}
          <div>
            <div className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3 mb-2">
              指标对比
            </div>
            <div className="h-[200px]">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={compData} layout="vertical" margin={{ top: 5, right: 40, left: 50, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 9, fill: "var(--t2)" }} tickLine={false} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 10, fill: "var(--t2)" }} tickLine={false} axisLine={false} width={45} />
                  <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                  <Bar dataKey="IS" fill="var(--info)" barSize={8} radius={[0, 2, 2, 0]} name="IS" />
                  <Bar dataKey="OOS" fill="var(--info)" barSize={8} radius={[0, 2, 2, 0]} name="OOS" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            {/* Degradation labels */}
            <div className="flex flex-wrap gap-3 mt-2">
              {compData.map((d) => (
                <span key={d.name} className="text-[9px] font-mono text-muted-foreground">
                  {d.name}:{" "}
                  <span style={{ color: d.degradation >= 0 ? "var(--suc)" : "var(--dan)" }}>
                    {d.degradation >= 0 ? "+" : ""}{d.degradation}%
                  </span>
                </span>
              ))}
            </div>
          </div>
        </div>
      </GlassCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Section 5: Parameter Analysis (Layer 3)                            */
/* ------------------------------------------------------------------ */

interface ParamSensitivityData {
  single_param?: Record<string, { bins: number[]; values: number[] }>;
  grid?: Record<string, { x_bins: number[]; y_bins: number[]; values: (number | null)[][]; x_label: string; y_label: string }>;
}

function ParamAnalysisSection({ sensitivity, stability, wfResults }: {
  sensitivity?: ParamSensitivityData | null;
  stability?: number | null;
  wfResults?: Array<{ fold: number; test_value: number; train_start: string; test_start: string; test_end: string }>;
}) {
  const hasSensitivity = sensitivity?.single_param && Object.keys(sensitivity.single_param).length > 0;
  const hasWf = wfResults && wfResults.length > 0;
  if (!hasSensitivity && !hasWf && stability == null) return null;

  return (
    <div {...cardAnim(0.28)}>
      <GlassCard className="p-5">
        <SectionLabel>
          Parameter Analysis
          {stability != null && (
            <span className="ml-3 text-[10px] font-mono text-muted-foreground">
              稳定性: {stability.toFixed(4)}
              <HelpTip text="最优参数附近 (±20%) 的 fitness 标准差。越低越稳定。" />
            </span>
          )}
        </SectionLabel>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Sensitivity: single param line charts */}
          {hasSensitivity && (
            <div>
              <div className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3 mb-2">
                参数敏感性
              </div>
              <div className="space-y-3">
                {Object.entries(sensitivity!.single_param!).slice(0, 4).map(([pname, data]) => (
                  <div key={pname}>
                    <div className="text-[10px] font-mono text-muted-foreground mb-1">{pname}</div>
                    <div className="h-[100px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={data.bins.map((b, i) => ({ x: b, fitness: data.values[i] }))} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" />
                          <XAxis dataKey="x" tick={{ fontSize: 8, fill: "var(--t2)" }} tickLine={false} />
                          <YAxis tick={{ fontSize: 8, fill: "var(--t2)" }} tickLine={false} />
                          <Line type="monotone" dataKey="fitness" stroke="var(--info)" strokeWidth={1.5} dot={{ fill: "var(--info)", r: 2 }} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Walk-forward fold results */}
          {hasWf && (
            <div>
              <div className="text-[9px] font-semibold tracking-[1px] uppercase text-qds-t3 mb-2">
                Walk-Forward Folds
              </div>
              <div className="h-[200px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={wfResults} layout="vertical" margin={{ top: 5, right: 10, left: 40, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--bd)" horizontal={false} />
                    <XAxis type="number" tick={{ fontSize: 9, fill: "var(--t2)" }} tickLine={false} />
                    <YAxis type="category" dataKey="fold" tick={{ fontSize: 10, fill: "var(--t2)" }} tickLine={false} axisLine={false} tickFormatter={(v: number) => `Fold ${v}`} width={45} />
                    <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                    <Bar dataKey="test_value" fill="var(--info)" barSize={12} radius={[0, 4, 4, 0]} name="OOS Fitness" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>
      </GlassCard>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Tab Component                                                 */
/* ------------------------------------------------------------------ */

export function RobustnessTab({ runId }: Props) {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    setLoading(true);
    setResult(null);

    fetch(`${API_BASE}/api/backtest/${runId}/result`, {
      headers: {
        ...(process.env.NEXT_PUBLIC_API_KEY
          ? { "x-api-key": process.env.NEXT_PUBLIC_API_KEY }
          : {}),
      },
    })
      .then((res) => res.json())
      .then((data) => {
        if (!cancelled) {
          setResult(data?.result ?? data ?? null);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [runId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-muted-foreground">加载中...</span>
      </div>
    );
  }

  const r = result?.robustness;

  if (!r) {
    return (
      <div className="flex items-center justify-center h-48">
        <div className="text-center">
          <AlertTriangle className="w-5 h-5 text-qds-t3 mx-auto mb-2" />
          <span className="text-xs text-muted-foreground">
            无健壮性数据（旧版回测结果不包含此信息）
          </span>
        </div>
      </div>
    );
  }

  // Optimization context (Layer 2/3) — loosely typed since optimization data
  // comes from a different API shape
  const opt = (result as unknown as Record<string, unknown> | null)?.optimization as IsOosData | undefined;

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Layer 1: Always visible for any backtest */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <StatisticalTestsSection r={r} />
        <McEquityConeSection r={r} />
      </div>

      <McSummarySection r={r} />

      {/* Layer 2: IS vs OOS (optimization runs only) */}
      {opt?.train_validation && opt?.validation && (
        <IsOosSection opt={opt} />
      )}

      {/* Layer 3: Parameter Analysis (optimization runs only) */}
      {opt && (
        <ParamAnalysisSection
          sensitivity={opt.parameter_sensitivity}
          stability={opt.parameter_stability_score}
          wfResults={opt.walk_forward_results}
        />
      )}
    </div>
  );
}
