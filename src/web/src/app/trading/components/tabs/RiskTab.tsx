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
import { motion } from "framer-motion";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";

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
    <div
      className={`rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-sm overflow-hidden ${className}`}
    >
      {children}
    </div>
  );
}

function KpiCard({
  label,
  value,
  sub,
  color,
  index,
}: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  index: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.06, ease: [0.22, 1, 0.36, 1] }}
    >
      <GlassCard className="p-4 hover:bg-white/[0.05] transition-all duration-300">
        <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-2">
          {label}
        </div>
        <div
          className="text-2xl font-bold font-heading tracking-tight"
          style={{ color: color ?? "var(--foreground)" }}
        >
          {value}
        </div>
        {sub && (
          <div className="text-[9px] text-muted-foreground/40 mt-1">{sub}</div>
        )}
      </GlassCard>
    </motion.div>
  );
}

function MarginBar({ pct }: { pct: number }) {
  const clamped = Math.min(100, Math.max(0, pct));
  const color =
    clamped > 80
      ? "var(--accent-red)"
      : clamped > 50
      ? "var(--accent-amber)"
      : "var(--accent-green)";

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
          保证金使用率
        </span>
        <span
          className="text-[12px] font-bold font-mono"
          style={{ color }}
        >
          {clamped.toFixed(1)}%
        </span>
      </div>
      <div className="h-2 rounded-full bg-white/[0.06] overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${clamped}%`,
            background: `linear-gradient(90deg, ${color}99, ${color})`,
          }}
        />
      </div>
      <div className="flex justify-between text-[9px] text-muted-foreground/30">
        <span>0%</span>
        <span className="text-[var(--accent-amber)]">50%</span>
        <span className="text-[var(--accent-red)]">80%</span>
        <span>100%</span>
      </div>
    </div>
  );
}

export function RiskTab({ nodeType }: Props) {
  const [metrics, setMetrics] = useState<RiskMetrics>({});
  const [equityPoints, setEquityPoints] = useState<EquityPoint[]>([]);
  const [dataStatus, setDataStatus] = useState<DataStatus>({});

  // Initial load
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

  // Compute drawdown series
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

  const fmtDollar = (v: number) =>
    v >= 1_000_000
      ? `$${(v / 1_000_000).toFixed(2)}M`
      : v >= 1_000
      ? `$${(v / 1_000).toFixed(1)}K`
      : `$${v.toFixed(0)}`;

  const fmtPct = (v?: number) => (v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}%` : "N/A");

  const ddColor = (metrics.drawdown_pct ?? 0) < 0 ? "var(--accent-red)" : "var(--foreground)";
  const pnlColor =
    (metrics.daily_pnl_pct ?? 0) >= 0 ? "var(--accent-green)" : "var(--accent-red)";

  return (
    <div className="flex flex-col gap-4 p-4 min-h-0">
      {/* Breach alert */}
      {metrics.breached && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="p-3 rounded-lg bg-red-500/10 border border-red-500/20"
        >
          <span className="text-[12px] font-bold text-red-400">
            风控告警: {metrics.breach_reason}
          </span>
        </motion.div>
      )}

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-3">
        <KpiCard
          label="最大回撤"
          value={fmtPct(metrics.drawdown_pct)}
          color={ddColor}
          index={0}
        />
        <KpiCard
          label="日度盈亏"
          value={fmtPct(metrics.daily_pnl_pct)}
          color={pnlColor}
          index={1}
        />
        <KpiCard
          label="总风险敞口"
          value={metrics.total_exposure != null ? fmtDollar(metrics.total_exposure) : "N/A"}
          color="var(--accent-blue)"
          index={2}
        />
        <KpiCard
          label="持仓数量"
          value={String(metrics.position_count ?? 0)}
          index={3}
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-[1fr_280px] gap-3">
        {/* Drawdown chart */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.25, ease: [0.22, 1, 0.36, 1] }}
        >
          <GlassCard className="p-4">
            <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-3">
              水下曲线 (Drawdown)
            </div>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={drawdownSeries} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#EF5350" stopOpacity={0.25} />
                    <stop offset="95%" stopColor="#EF5350" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis
                  dataKey="ts"
                  tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => {
                    try { return new Date(v).toLocaleDateString("zh-CN", { month: "short", day: "numeric" }); }
                    catch { return v; }
                  }}
                />
                <YAxis
                  tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v.toFixed(1)}%`}
                  width={48}
                />
                <RechartsTooltip
                  contentStyle={tooltipStyle}
                  labelStyle={{ color: "rgba(255,255,255,0.5)" }}
                  formatter={(v: unknown) => [`${(v as number).toFixed(2)}%`, "回撤"]}
                />
                {metrics.drawdown_threshold != null && (
                  <ReferenceLine
                    y={-Math.abs(metrics.drawdown_threshold)}
                    stroke="#F0B429"
                    strokeDasharray="4 4"
                    strokeOpacity={0.5}
                    label={{ value: "阈值", fill: "#F0B429", fontSize: 9 }}
                  />
                )}
                <Area
                  type="monotone"
                  dataKey="drawdown"
                  stroke="#EF5350"
                  strokeWidth={1.5}
                  fill="url(#ddGrad)"
                  animationDuration={1500}
                  animationEasing="ease-out"
                />
              </AreaChart>
            </ResponsiveContainer>
          </GlassCard>
        </motion.div>

        {/* Exposure per instrument */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.3, ease: [0.22, 1, 0.36, 1] }}
        >
          <GlassCard className="p-4 h-full">
            <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-3">
              各品种敞口
            </div>
            {exposureEntries.length > 0 ? (
              <ResponsiveContainer width="100%" height={180}>
                <BarChart
                  data={exposureEntries}
                  layout="vertical"
                  margin={{ top: 0, right: 8, bottom: 0, left: 0 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(255,255,255,0.04)"
                    horizontal={false}
                  />
                  <XAxis
                    type="number"
                    tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) =>
                      v >= 1000 ? `${(v / 1000).toFixed(0)}K` : String(v)
                    }
                  />
                  <YAxis
                    type="category"
                    dataKey="instrument"
                    tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 9 }}
                    axisLine={false}
                    tickLine={false}
                    width={80}
                    tickFormatter={(v: string) => v.replace(".BINANCE", "").replace("-PERP", "")}
                  />
                  <RechartsTooltip
                    contentStyle={tooltipStyle}
                    labelStyle={{ color: "rgba(255,255,255,0.5)" }}
                    formatter={(v: unknown) => [fmtDollar(v as number), "敞口"]}
                  />
                  <Bar dataKey="exposure" radius={[0, 3, 3, 0]} animationDuration={1200}>
                    {exposureEntries.map((_, i) => (
                      <Cell key={i} fill="#4C9EEB" fillOpacity={0.8 - i * 0.08} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex items-center justify-center h-[180px] text-muted-foreground/30 text-xs">
                暂无持仓数据
              </div>
            )}
          </GlassCard>
        </motion.div>
      </div>

      {/* Margin gauge */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.35, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <MarginBar pct={marginPct} />
        </GlassCard>
      </motion.div>
    </div>
  );
}
