"use client";

import { useEffect, useState } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { motion } from "framer-motion";
import { apiGet } from "@/lib/api";

interface Props {
  nodeType: "sandbox" | "live";
}

interface EquityPoint {
  ts: string;
  equity: number;
}

interface BacktestData {
  equity_curve: EquityPoint[];
  stats: Record<string, number | null>;
  run_id: string;
  strategy_name: string;
}

interface CompareResult {
  backtest: BacktestData | null;
  paper: { equity_curve: EquityPoint[] };
  comparison: {
    backtest_pnl: number | null;
    backtest_win_rate: number | null;
    backtest_sharpe: number | null;
    paper_equity_points: number;
    paper_latest_equity: number | null;
  } | null;
  warning: string | null;
}

interface StrategyInfo {
  name: string;
  state: string;
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

function GlassCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-sm overflow-hidden ${className}`}>
      {children}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-3">
      {children}
    </div>
  );
}

function MetricRow({
  label,
  btValue,
  paperValue,
}: {
  label: string;
  btValue: string;
  paperValue: string;
}) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-white/[0.04] last:border-0">
      <span className="text-[11px] text-muted-foreground/60">{label}</span>
      <div className="flex gap-8">
        <span className="text-[11px] font-mono text-[var(--accent-blue)] w-20 text-right">{btValue}</span>
        <span className="text-[11px] font-mono text-[var(--accent-green)] w-20 text-right">{paperValue}</span>
      </div>
    </div>
  );
}

function mergeEquitySeries(
  btPoints: EquityPoint[],
  paperPoints: EquityPoint[]
): { pct: number; backtest?: number; paper?: number }[] {
  // Normalize both series to index (0–100) for overlay comparison
  const btLen = btPoints.length;
  const paperLen = paperPoints.length;
  const maxLen = Math.max(btLen, paperLen);
  if (maxLen === 0) return [];

  const result: { pct: number; backtest?: number; paper?: number }[] = [];
  for (let i = 0; i < maxLen; i++) {
    const pct = Math.round((i / (maxLen - 1 || 1)) * 100);
    const btIdx = btLen > 0 ? Math.round((i / (maxLen - 1 || 1)) * (btLen - 1)) : -1;
    const paperIdx = paperLen > 0 ? Math.round((i / (maxLen - 1 || 1)) * (paperLen - 1)) : -1;
    result.push({
      pct,
      backtest: btIdx >= 0 ? btPoints[btIdx].equity : undefined,
      paper: paperIdx >= 0 ? paperPoints[paperIdx].equity : undefined,
    });
  }
  return result;
}

const fmtPct = (v: number | null | undefined) =>
  v != null ? `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%` : "N/A";

const fmtNum = (v: number | null | undefined, decimals = 2) =>
  v != null ? v.toFixed(decimals) : "N/A";

const fmtDollar = (v: number | null | undefined) => {
  if (v == null) return "N/A";
  return v >= 0 ? `+$${v.toFixed(2)}` : `-$${Math.abs(v).toFixed(2)}`;
};

export function BacktestCompareTab({ nodeType }: Props) {
  const [strategies, setStrategies] = useState<string[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<string>("");
  const [result, setResult] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState(false);

  // Load strategy list for strategy selector
  useEffect(() => {
    let cancelled = false;
    apiGet<{ strategies: Record<string, StrategyInfo> }>("/api/node/strategies", { mode: nodeType })
      .then((data) => {
        if (cancelled || !data) return;
        const names = Object.keys(data.strategies ?? {});
        setStrategies(names);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [nodeType]);

  // Fetch compare data when strategy selected
  useEffect(() => {
    if (!selectedStrategy) {
      setResult(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    apiGet<CompareResult>("/api/backtest/compare", {
      strategy_name: selectedStrategy,
      node_type: nodeType,
    })
      .then((data) => {
        if (cancelled) return;
        setResult(data);
      })
      .catch(() => {
        if (!cancelled) setResult(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [selectedStrategy, nodeType]);

  const btPoints = result?.backtest?.equity_curve ?? [];
  const paperPoints = result?.paper?.equity_curve ?? [];
  const chartData = mergeEquitySeries(btPoints, paperPoints);

  const hasBt = btPoints.length > 0;
  const hasPaper = paperPoints.length > 0;
  const hasComparison = result?.comparison != null;

  return (
    <div className="flex flex-col gap-4 p-4 min-h-0">
      {/* Strategy selector */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <SectionLabel>选择策略</SectionLabel>
          <div className="flex items-center gap-3">
            <select
              value={selectedStrategy}
              onChange={(e) => setSelectedStrategy(e.target.value)}
              className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-[13px] text-foreground focus:outline-none focus:border-[var(--accent-blue)]/50 transition-colors"
            >
              <option value="">— 请选择策略 —</option>
              {strategies.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            {loading && (
              <span className="text-[11px] text-muted-foreground/50 animate-pulse">加载中...</span>
            )}
          </div>
          {result?.warning && (
            <div className="mt-2 text-[11px] text-[var(--accent-amber)]/70 bg-[var(--accent-amber)]/5 rounded-lg px-3 py-2 border border-[var(--accent-amber)]/10">
              {result.warning}
            </div>
          )}
        </GlassCard>
      </motion.div>

      {/* Empty state: no strategy selected */}
      {!selectedStrategy && !loading && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center justify-center py-16 text-muted-foreground/30"
        >
          <div className="text-4xl mb-3">📊</div>
          <div className="text-[13px]">请选择策略以开始比较</div>
          <div className="text-[11px] mt-1">回测曲线与沙盒实盘将叠加显示</div>
        </motion.div>
      )}

      {/* Empty state: strategy selected but no data */}
      {selectedStrategy && !loading && result && !hasBt && !hasPaper && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="flex flex-col items-center justify-center py-16 text-muted-foreground/30"
        >
          <div className="text-4xl mb-3">🔍</div>
          <div className="text-[13px]">暂无可比较的数据</div>
          <div className="text-[11px] mt-1">请先运行回测并启动沙盒</div>
        </motion.div>
      )}

      {/* Overlay equity chart */}
      {(hasBt || hasPaper) && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.1, ease: [0.22, 1, 0.36, 1] }}
        >
          <GlassCard className="p-4">
            <SectionLabel>权益曲线对比</SectionLabel>
            <div className="flex items-center gap-4 mb-3">
              {hasBt && (
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-0.5 rounded bg-[var(--accent-blue)]" />
                  <span className="text-[10px] text-muted-foreground/60">回测</span>
                </div>
              )}
              {hasPaper && (
                <div className="flex items-center gap-1.5">
                  <div className="w-3 h-0.5 rounded bg-[var(--accent-green)]" />
                  <span className="text-[10px] text-muted-foreground/60">沙盒实盘</span>
                </div>
              )}
              <span className="text-[9px] text-muted-foreground/30 ml-auto">X轴为进度百分比（已对齐）</span>
            </div>
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#4C9EEB" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#4C9EEB" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="paperGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#26D97F" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#26D97F" stopOpacity={0} />
                  </linearGradient>
                  <filter id="btGlow">
                    <feGaussianBlur stdDeviation="3" result="coloredBlur" />
                    <feMerge>
                      <feMergeNode in="coloredBlur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis
                  dataKey="idx"
                  tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                />
                <YAxis
                  tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) =>
                    v >= 1_000_000
                      ? `$${(v / 1_000_000).toFixed(1)}M`
                      : v >= 1_000
                      ? `$${(v / 1_000).toFixed(0)}K`
                      : `$${v.toFixed(0)}`
                  }
                  width={56}
                />
                <RechartsTooltip
                  contentStyle={tooltipStyle}
                  labelStyle={{ color: "rgba(255,255,255,0.5)" }}
                  labelFormatter={(v) => `进度 ${v}%`}
                  formatter={(v: unknown) => [
                    `$${(v as number).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                    "",
                  ]}
                />
                {hasBt && (
                  <Area
                    type="monotone"
                    dataKey="backtest"
                    stroke="#4C9EEB"
                    strokeWidth={1.5}
                    fill="url(#btGrad)"
                    dot={false}
                    animationDuration={1500}
                    animationEasing="ease-out"
                  />
                )}
                {hasPaper && (
                  <Area
                    type="monotone"
                    dataKey="paper"
                    stroke="#26D97F"
                    strokeWidth={1.5}
                    fill="url(#paperGrad)"
                    dot={false}
                    animationDuration={1500}
                    animationEasing="ease-out"
                  />
                )}
              </AreaChart>
            </ResponsiveContainer>
          </GlassCard>
        </motion.div>
      )}

      {/* Comparison metrics table */}
      {hasComparison && (
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.2, ease: [0.22, 1, 0.36, 1] }}
        >
          <GlassCard className="p-4">
            <SectionLabel>指标对比</SectionLabel>
            {/* Header */}
            <div className="flex items-center justify-between pb-2 border-b border-white/[0.06] mb-1">
              <span className="text-[10px] text-muted-foreground/40">指标</span>
              <div className="flex gap-8">
                <span className="text-[10px] font-semibold text-[var(--accent-blue)] w-20 text-right">回测</span>
                <span className="text-[10px] font-semibold text-[var(--accent-green)] w-20 text-right">沙盒实盘</span>
              </div>
            </div>
            <MetricRow
              label="总盈亏"
              btValue={fmtDollar(result!.comparison!.backtest_pnl)}
              paperValue={
                result!.comparison!.paper_latest_equity != null
                  ? `$${result!.comparison!.paper_latest_equity.toFixed(2)}`
                  : "N/A"
              }
            />
            <MetricRow
              label="胜率"
              btValue={fmtPct(result!.comparison!.backtest_win_rate)}
              paperValue="—"
            />
            <MetricRow
              label="夏普比率"
              btValue={fmtNum(result!.comparison!.backtest_sharpe)}
              paperValue="—"
            />
            <MetricRow
              label="回测数据点"
              btValue={String(btPoints.length)}
              paperValue={String(result!.comparison!.paper_equity_points)}
            />
          </GlassCard>
        </motion.div>
      )}

      {/* Individual data availability notices */}
      {selectedStrategy && result && !loading && (hasBt || hasPaper) && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.3 }}
          className="flex gap-3"
        >
          <div className={`flex-1 rounded-lg px-3 py-2 text-[11px] border ${hasBt ? "border-[var(--accent-blue)]/20 bg-[var(--accent-blue)]/5 text-[var(--accent-blue)]/70" : "border-white/[0.04] bg-white/[0.01] text-muted-foreground/30"}`}>
            {hasBt ? `✓ 回测数据 (${btPoints.length} 点)` : "✗ 无回测数据"}
          </div>
          <div className={`flex-1 rounded-lg px-3 py-2 text-[11px] border ${hasPaper ? "border-[var(--accent-green)]/20 bg-[var(--accent-green)]/5 text-[var(--accent-green)]/70" : "border-white/[0.04] bg-white/[0.01] text-muted-foreground/30"}`}>
            {hasPaper ? `✓ 沙盒数据 (${paperPoints.length} 点)` : "✗ 无沙盒实盘数据"}
          </div>
        </motion.div>
      )}
    </div>
  );
}
