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
  BarChart,
  Bar,
  Cell,
} from "recharts";
import { apiGet } from "@/lib/api";
import { CHART_TOOLTIP_PROPS, CHART_LABEL_STYLE, CHART_GRID_STYLE } from "@/lib/chartTheme";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { FadeIn } from "@/components/motion/FadeIn";

interface Props {
  nodeType: "sandbox" | "live";
}

interface RiskMetrics {
  breached?: boolean;
  breach_reason?: string;
  drawdown_pct?: number;
  daily_pnl_pct?: number;
  total_exposure?: number;
  position_count?: number;
  per_instrument_exposure?: Record<string, number>;
  daily_loss_threshold?: number;
  drawdown_threshold?: number;
}

interface EquityPoint {
  ts: string;
  equity: number;
}

interface DataStatus {
  balance_total?: number;
  balance_free?: number;
}


function GaugeBar({ label, pct, thresholds }: { label: string; pct: number; thresholds?: { warn: number; danger: number } }) {
  const clamped = Math.min(100, Math.max(0, pct));
  const warn = thresholds?.warn ?? 30;
  const danger = thresholds?.danger ?? 70;
  const color = clamped > danger ? "var(--dan)" : clamped > warn ? "var(--warn)" : "var(--suc)";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="qds-section-label">{label}</span>
        <span className="text-[0.72rem] font-bold font-mono" style={{ color }}>{clamped.toFixed(1)}%</span>
      </div>
      <div className="h-2 rounded-full bg-secondary overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${clamped}%`, background: `linear-gradient(90deg, color-mix(in srgb, ${color} 60%, transparent), ${color})`, transitionDuration: "500ms" }}
        />
      </div>
      <div className="flex justify-between text-[0.56rem] text-qds-t3">
        <span>0%</span>
        <span style={{ color: "var(--warn)" }}>{warn}%</span>
        <span style={{ color: "var(--dan)" }}>{danger}%</span>
        <span>100%</span>
      </div>
    </div>
  );
}

export function RiskTab({ nodeType }: Props) {
  const [metrics, setMetrics] = useState<RiskMetrics>({});
  const [equityPoints, setEquityPoints] = useState<EquityPoint[]>([]);
  const [dataStatus, setDataStatus] = useState<DataStatus>({});

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [rm, eq, ds] = await Promise.all([
          apiGet<RiskMetrics>("/api/trading/risk-metrics", { node_type: nodeType }),
          apiGet<{ points: EquityPoint[] }>("/api/trading/equity", { node_type: nodeType }),
          apiGet<DataStatus>("/api/node/data-status", { mode: nodeType }),
        ]);
        if (cancelled) return;
        if (rm) setMetrics(rm);
        if (eq?.points) setEquityPoints(eq.points);
        if (ds) setDataStatus(ds);
      } catch {
        // silent
      }
    }
    load();
    return () => { cancelled = true; };
  }, [nodeType]);

  // Real-time risk metrics via WS
  const riskMsg = useWsEvent("risk.metrics");
  useEffect(() => {
    if (!riskMsg) return;
    const d = (riskMsg.data ?? riskMsg) as RiskMetrics & { node_type?: string };
    if (d.node_type && d.node_type !== nodeType) return;
    setMetrics((prev) => ({ ...prev, ...d }));
  }, [riskMsg, nodeType]);

  const drawdownSeries = useMemo(() => {
    if (equityPoints.length === 0) return [];
    let peak = -Infinity;
    return equityPoints.map((pt) => {
      if (pt.equity > peak) peak = pt.equity;
      const dd = peak > 0 ? ((pt.equity - peak) / peak) * 100 : 0;
      return { ts: pt.ts, drawdown: parseFloat(dd.toFixed(4)) };
    });
  }, [equityPoints]);

  const marginPct = useMemo(() => {
    const total = dataStatus.balance_total ?? 0;
    const free = dataStatus.balance_free ?? 0;
    if (total <= 0) return 0;
    return ((total - free) / total) * 100;
  }, [dataStatus]);

  const exposureEntries = Object.entries(metrics.per_instrument_exposure ?? {})
    .map(([instrument, exposure]) => ({ instrument, exposure }))
    .sort((a, b) => b.exposure - a.exposure);

  const fmtDollar = (v: number) => v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(2)}M` : v >= 1_000 ? `$${(v / 1_000).toFixed(1)}K` : `$${v.toFixed(0)}`;
  const fmtPct = (v?: number) => (v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "N/A");

  const ddColor = (metrics.drawdown_pct ?? 0) < 0 ? "var(--dan)" : "var(--t0)";
  const pnlColor = (metrics.daily_pnl_pct ?? 0) >= 0 ? "var(--suc)" : "var(--dan)";

  return (
    <div className="flex flex-col gap-5 p-5 min-h-0">
      {/* Breach alert */}
      {metrics.breached && (
        <div className="p-3 rounded-lg bg-qds-danger-dim border border-destructive" style={{ borderColor: "color-mix(in srgb, var(--dan) 30%, transparent)" }}>
          <span className="text-[0.72rem] font-bold text-destructive">风控告警: {metrics.breach_reason}</span>
        </div>
      )}

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "日亏损限额使用", value: fmtPct(metrics.daily_pnl_pct), color: pnlColor },
          { label: "最大回撤", value: fmtPct(metrics.drawdown_pct), color: ddColor },
          { label: "总风险敞口", value: metrics.total_exposure != null ? fmtDollar(metrics.total_exposure) : "N/A", color: "var(--info)" },
          { label: "持仓数量", value: String(metrics.position_count ?? 0), color: "var(--t0)" },
        ].map((kpi, i) => (
          <FadeIn key={kpi.label} delay={i * 0.05}>
            <div className="rounded-lg border bg-card p-3 hover:bg-secondary transition-colors" style={{ transitionDuration: "var(--dur)" }}>
              <div className="qds-stat-label">{kpi.label}</div>
              <div className="text-[1.1rem] font-bold font-mono" style={{ color: kpi.color }}>{kpi.value}</div>
            </div>
          </FadeIn>
        ))}
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-[1fr_280px] gap-3">
        {/* Drawdown chart */}
        <FadeIn delay={0.25}>
          <div className="rounded-lg border bg-card overflow-hidden">
            <div className="qds-card-header">
              <span>水下曲线 (Drawdown)</span>
            </div>
            <div className="p-4">
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={drawdownSeries} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="var(--dan)" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="var(--dan)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid {...CHART_GRID_STYLE} />
                  <XAxis dataKey="ts" tick={{ fill: "var(--t3)", fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={(v) => { try { return new Date(v).toLocaleDateString("zh-CN", { month: "short", day: "numeric" }); } catch { return v; } }} />
                  <YAxis tick={{ fill: "var(--t3)", fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={(v) => `${v.toFixed(1)}%`} width={48} />
                  <RechartsTooltip {...CHART_TOOLTIP_PROPS} formatter={(v: unknown) => [`${(v as number).toFixed(2)}%`, "回撤"]} />
                  {metrics.drawdown_threshold != null && (
                    <ReferenceLine y={-Math.abs(metrics.drawdown_threshold)} stroke="var(--warn)" strokeDasharray="4 4" strokeOpacity={0.5} label={{ ...CHART_LABEL_STYLE, value: "阈值", position: "insideTopRight", fill: "var(--warn)" }} />
                  )}
                  <Area type="monotone" dataKey="drawdown" stroke="var(--dan)" strokeWidth={1.5} fill="url(#ddGrad)" animationDuration={1500} animationEasing="ease-out" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>
        </FadeIn>

        {/* Exposure per instrument */}
        <FadeIn delay={0.3}>
          <div className="rounded-lg border bg-card overflow-hidden h-full">
            <div className="qds-card-header">
              <span>各品种敞口</span>
            </div>
            <div className="p-4">
              {exposureEntries.length > 0 ? (
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={exposureEntries} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid {...CHART_GRID_STYLE} horizontal={false} />
                    <XAxis type="number" tick={{ fill: "var(--t3)", fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={(v) => v >= 1000 ? `${(v / 1000).toFixed(0)}K` : String(v)} />
                    <YAxis type="category" dataKey="instrument" tick={{ fill: "var(--t2)", fontSize: 9 }} axisLine={false} tickLine={false} width={80} tickFormatter={(v: string) => v.replace(".BINANCE", "").replace("-PERP", "")} />
                    <RechartsTooltip {...CHART_TOOLTIP_PROPS} formatter={(v: unknown) => [fmtDollar(v as number), "敞口"]} />
                    <Bar dataKey="exposure" radius={[0, 3, 3, 0]} animationDuration={1200}>
                      {exposureEntries.map((_, i) => (
                        <Cell key={i} fill="var(--info)" fillOpacity={0.8 - i * 0.08} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex items-center justify-center h-[180px] text-qds-t3 text-[0.72rem]">暂无持仓数据</div>
              )}
            </div>
          </div>
        </FadeIn>
      </div>

      {/* Gauge bars */}
      <FadeIn delay={0.35}>
        <div className="rounded-lg border bg-card p-4 space-y-5">
          <GaugeBar label="保证金使用率" pct={marginPct} thresholds={{ warn: 50, danger: 80 }} />
          {metrics.daily_loss_threshold != null && metrics.daily_pnl_pct != null && (
            <GaugeBar label="日亏损限额" pct={Math.abs(metrics.daily_pnl_pct / metrics.daily_loss_threshold * 100)} thresholds={{ warn: 30, danger: 70 }} />
          )}
        </div>
      </FadeIn>
    </div>
  );
}
