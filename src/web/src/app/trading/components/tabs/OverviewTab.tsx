"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Inbox } from "lucide-react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { PositionsTable } from "../PositionsTable";
import type { Position } from "../../page";

interface Props {
  nodeType: "sandbox" | "live";
  positions: Position[];
  loading: boolean;
}

interface EquityPoint {
  ts: string;
  equity: number;
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

function fmtEquity(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(2)}K`;
  return `$${v.toFixed(2)}`;
}

function fmtPnl(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}`;
}

function fmtTime(ts: string, rangeHours: number): string {
  try {
    const d = new Date(ts);
    if (rangeHours > 48) {
      return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
    }
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts;
  }
}

export function OverviewTab({ nodeType, positions, loading }: Props) {
  const [equityPoints, setEquityPoints] = useState<EquityPoint[]>([]);
  const [equityLoading, setEquityLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setEquityLoading(true);
    setEquityPoints([]);

    apiGet<{ points: EquityPoint[] } | EquityPoint[]>("/api/trading/equity", {
      node_type: nodeType,
      limit: "1000",
    })
      .then((res) => {
        if (cancelled) return;
        if (Array.isArray(res)) {
          setEquityPoints(res);
        } else if (res?.points) {
          setEquityPoints(res.points);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setEquityLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [nodeType]);

  // Real-time equity append via WS
  const equityMsg = useWsEvent("equity.snapshot");
  useEffect(() => {
    if (!equityMsg) return;
    const d = (equityMsg.data ?? equityMsg) as { node_type?: string; ts?: string; equity?: number };
    if (d?.node_type && d.node_type !== nodeType) return;
    if (d?.ts && d?.equity != null) {
      setEquityPoints((prev) => [...prev, { ts: d.ts!, equity: d.equity! }].slice(-1000));
    }
  }, [equityMsg, nodeType]);

  // Compute summary metrics from positions
  const totalRealizedPnl = positions.reduce(
    (sum, p) => sum + (p.realized_pnl ?? 0),
    0
  );
  const totalUnrealizedPnl = positions.reduce(
    (sum, p) => sum + (p.unrealized_pnl ?? 0),
    0
  );

  const latestEquity =
    equityPoints.length > 0 ? equityPoints[equityPoints.length - 1].equity : null;

  // Determine time range for X-axis formatting
  const rangeHours =
    equityPoints.length >= 2
      ? (new Date(equityPoints[equityPoints.length - 1].ts).getTime() -
          new Date(equityPoints[0].ts).getTime()) /
        3_600_000
      : 0;

  const kpiCards = [
    {
      label: "总权益",
      value: latestEquity != null ? fmtEquity(latestEquity) : "—",
      color: "var(--foreground)" as string,
    },
    {
      label: "已实现 PnL",
      value: fmtPnl(totalRealizedPnl),
      color:
        totalRealizedPnl >= 0 ? "var(--accent-green)" : "var(--accent-red)",
    },
    {
      label: "持仓数",
      value: String(positions.length),
      color: "var(--accent-blue)" as string,
    },
    {
      label: "未实现 PnL",
      value: fmtPnl(totalUnrealizedPnl),
      color:
        totalUnrealizedPnl >= 0 ? "var(--accent-green)" : "var(--accent-red)",
    },
  ];

  // Starting balance reference line
  const startEquity = equityPoints.length > 0 ? equityPoints[0].equity : null;

  return (
    <div className="flex flex-col gap-4 p-4 min-h-0">
      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-3">
        {kpiCards.map((card, i) => (
          <motion.div
            key={card.label}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4, delay: i * 0.06, ease: [0.22, 1, 0.36, 1] }}
          >
            <GlassCard className="p-5 hover:bg-white/[0.05] transition-all duration-300">
              <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/60 mb-2">
                {card.label}
              </div>
              <div
                className="text-2xl font-bold font-heading tracking-tight"
                style={{ color: card.color }}
              >
                {loading ? (
                  <span className="opacity-30">—</span>
                ) : (
                  card.value
                )}
              </div>
            </GlassCard>
          </motion.div>
        ))}
      </div>

      {/* Equity curve */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.25, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-3">
            权益曲线
          </div>
          {equityLoading ? (
            <div className="flex items-center justify-center h-[280px] text-muted-foreground/30 text-xs">
              加载中...
            </div>
          ) : equityPoints.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-[280px] gap-2 text-muted-foreground/30">
              <Inbox className="w-7 h-7 opacity-40" />
              <span className="text-xs">暂无权益数据</span>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart
                data={equityPoints}
                margin={{ top: 4, right: 8, bottom: 0, left: 0 }}
              >
                <defs>
                  <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#4C9EEB" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#4C9EEB" stopOpacity={0} />
                  </linearGradient>
                  <filter id="equityGlow">
                    <feGaussianBlur stdDeviation="3" result="blur" />
                    <feComposite in="SourceGraphic" in2="blur" operator="over" />
                  </filter>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="rgba(255,255,255,0.04)"
                />
                <XAxis
                  dataKey="ts"
                  tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => fmtTime(v, rangeHours)}
                  minTickGap={60}
                />
                <YAxis
                  tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 9 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => fmtEquity(v)}
                  width={64}
                />
                <RechartsTooltip
                  contentStyle={tooltipStyle}
                  labelStyle={{ color: "rgba(255,255,255,0.5)" }}
                  labelFormatter={(v) => fmtTime(v as string, rangeHours)}
                  formatter={(v: unknown) => [fmtEquity(v as number), "权益"]}
                />
                {startEquity != null && (
                  <ReferenceLine
                    y={startEquity}
                    stroke="#F0B429"
                    strokeDasharray="4 4"
                    strokeOpacity={0.4}
                  />
                )}
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke="#4C9EEB"
                  strokeWidth={1.5}
                  fill="url(#equityGrad)"
                  animationDuration={1500}
                  animationEasing="ease-out"
                  dot={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </GlassCard>
      </motion.div>

      {/* Positions table */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, delay: 0.35, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard>
          <div className="flex items-center gap-2 px-4 pt-4 pb-0">
            <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
              当前持仓
            </span>
            <span
              className="px-1.5 py-0.5 rounded text-[9px] font-bold"
              style={{
                color: "var(--accent-blue)",
                backgroundColor: "rgba(76,158,235,0.12)",
              }}
            >
              {positions.length}
            </span>
          </div>
          {loading ? (
            <div className="p-4 space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-8 rounded bg-white/[0.03] animate-pulse" />
              ))}
            </div>
          ) : positions.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2 text-muted-foreground/30">
              <Inbox className="w-7 h-7 opacity-40" />
              <span className="text-xs">暂无持仓</span>
            </div>
          ) : (
            <PositionsTable
              positions={positions.map((p) => ({
                position_id: p.position_id,
                instrument_id: p.instrument_id,
                side: (p.side === "LONG" || p.side === "SHORT" ? p.side : "LONG") as "LONG" | "SHORT",
                quantity: p.quantity,
                avg_px_open: p.avg_px_open != null ? String(p.avg_px_open) : "—",
                unrealized_pnl:
                  p.unrealized_pnl != null ? p.unrealized_pnl.toFixed(2) : "0.00",
                unrealized_pnl_value: p.unrealized_pnl ?? 0,
                duration: p.duration ?? "",
              }))}
            />
          )}
        </GlassCard>
      </motion.div>
    </div>
  );
}
