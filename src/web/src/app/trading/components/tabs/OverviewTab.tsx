"use client";

import { useState, useEffect } from "react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
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
import { CHART_TOOLTIP_PROPS } from "@/lib/chartTheme";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { TickFlash } from "@/components/TickFlash";
import { EmptyState } from "@/components/EmptyState";
import { FadeIn } from "@/components/motion/FadeIn";
import type { Position, Fill } from "../../page";

interface Props {
  nodeType: "sandbox" | "live";
  positions: Position[];
  fills: Fill[];
  loading: boolean;
  onSelectStrategy: (id: string) => void;
  positionsOnly?: boolean;
}

interface EquityPoint {
  ts: string;
  equity: number;
}


function fmtEquity(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function fmtPnl(v: number): string {
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function fmtTime(ts: string, rangeHours: number): string {
  try {
    const d = new Date(ts);
    if (rangeHours > 48) return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return ts;
  }
}

function fmtFillTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

export function OverviewTab({ nodeType, positions, fills, loading, onSelectStrategy, positionsOnly }: Props) {
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
        if (Array.isArray(res)) setEquityPoints(res);
        else if (res?.points) setEquityPoints(res.points);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setEquityLoading(false); });
    return () => { cancelled = true; };
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

  const totalRealizedPnl = positions.reduce((sum, p) => sum + (p.realized_pnl ?? 0), 0);
  const totalUnrealizedPnl = positions.reduce((sum, p) => sum + (p.unrealized_pnl ?? 0), 0);
  const latestEquity = equityPoints.length > 0 ? equityPoints[equityPoints.length - 1].equity : null;
  const startEquity = equityPoints.length > 0 ? equityPoints[0].equity : null;

  const rangeHours = equityPoints.length >= 2
    ? (new Date(equityPoints[equityPoints.length - 1].ts).getTime() - new Date(equityPoints[0].ts).getTime()) / 3_600_000
    : 0;

  // Group positions by strategy
  const strategyMap = new Map<string, { positions: Position[]; realizedPnl: number; unrealizedPnl: number }>();
  for (const p of positions) {
    const tag = p.strategy_id_tag || "unknown";
    const entry = strategyMap.get(tag) ?? { positions: [], realizedPnl: 0, unrealizedPnl: 0 };
    entry.positions.push(p);
    entry.realizedPnl += p.realized_pnl ?? 0;
    entry.unrealizedPnl += p.unrealized_pnl ?? 0;
    strategyMap.set(tag, entry);
  }

  const pnlPct = startEquity && startEquity > 0 ? ((totalRealizedPnl / startEquity) * 100) : 0;

  const kpis = [
    { label: "总权益", value: latestEquity != null ? fmtEquity(latestEquity) : "—", color: "var(--t0)", sub: "" },
    { label: "已实现 PnL", value: fmtPnl(totalRealizedPnl), color: totalRealizedPnl >= 0 ? "var(--suc)" : "var(--dan)", sub: pnlPct !== 0 ? `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%` : "" },
    { label: "未实现 PnL", value: fmtPnl(totalUnrealizedPnl), color: totalUnrealizedPnl >= 0 ? "var(--suc)" : "var(--dan)", sub: "" },
    { label: "持仓", value: String(positions.length), color: "var(--t0)", sub: "" },
    { label: "今日成交", value: String(fills.length), color: "var(--t0)", sub: "" },
  ];

  // Positions-only mode: show full positions table
  if (positionsOnly) {
    const longPositions = positions.filter((p) => p.side === "LONG");
    const shortPositions = positions.filter((p) => p.side === "SHORT");
    const longPnl = longPositions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
    const shortPnl = shortPositions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);

    return (
      <div className="flex flex-col gap-5 p-5 min-h-0">
        {/* Position KPIs */}
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: `多头持仓 ${longPositions.length}`, value: fmtPnl(longPnl), color: longPnl >= 0 ? "var(--suc)" : "var(--dan)" },
            { label: `空头持仓 ${shortPositions.length}`, value: fmtPnl(shortPnl), color: shortPnl >= 0 ? "var(--suc)" : "var(--dan)" },
            { label: "总市值", value: latestEquity != null ? fmtEquity(latestEquity) : "—", color: "var(--t0)" },
            { label: "保证金", value: "—", color: "var(--t2)" },
          ].map((kpi, i) => (
            <FadeIn key={kpi.label} delay={i * 0.05}>
              <div className="rounded-lg border bg-card p-3 hover:bg-secondary transition-colors" style={{ transitionDuration: "var(--dur)" }}>
                <div className="qds-stat-label">{kpi.label}</div>
                <div className="text-[1.1rem] font-bold font-mono" style={{ color: kpi.color }}>{kpi.value}</div>
              </div>
            </FadeIn>
          ))}
        </div>

        {/* Positions table */}
        <FadeIn delay={0.2}>
          <div className="rounded-lg border bg-card overflow-hidden">
            <div className="qds-card-header">
              <span>持仓列表</span>
            </div>
            {positions.length === 0 ? (
              <EmptyState variant="first-use" title="暂无持仓" description="策略运行后将在此显示持仓信息" className="py-10" />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {["品种", "策略", "方向", "数量", "开仓均价", "标记价", "未实现PnL", "PnL%", "时长"].map((h) => (
                        <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {positions.map((p) => {
                      const isLong = p.side === "LONG";
                      const pnlVal = p.unrealized_pnl ?? 0;
                      return (
                        <TableRow key={p.position_id}>
                          <TableCell className="font-mono font-semibold whitespace-nowrap">{p.instrument_id}</TableCell>
                          <TableCell className="font-mono whitespace-nowrap">{p.strategy_id_tag || "—"}</TableCell>
                          <TableCell className={`font-bold whitespace-nowrap ${isLong ? "text-qds-success" : "text-destructive"}`}>{isLong ? "多" : "空"}</TableCell>
                          <TableCell className="font-mono whitespace-nowrap">{p.quantity}</TableCell>
                          <TableCell className="font-mono whitespace-nowrap">{p.avg_px_open ?? "—"}</TableCell>
                          <TableCell className="font-mono whitespace-nowrap">—</TableCell>
                          <TableCell className="whitespace-nowrap">
                            <TickFlash value={pnlVal}>
                              <span className={`font-mono font-semibold ${pnlVal >= 0 ? "text-qds-success" : "text-destructive"}`}>{fmtPnl(pnlVal)}</span>
                            </TickFlash>
                          </TableCell>
                          <TableCell className="font-mono whitespace-nowrap">—</TableCell>
                          <TableCell className="font-mono whitespace-nowrap">{p.duration ?? "—"}</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </FadeIn>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 p-5 min-h-0">
      {/* KPI cards */}
      <div className="grid grid-cols-5 gap-[1rem]">
        {kpis.map((kpi, i) => (
          <FadeIn key={kpi.label} delay={i * 0.05}>
            <div
              className="rounded-lg border bg-card py-[.8rem] px-[1rem] hover:border-qds-border-hover transition-[border-color]"
              style={{ transitionDuration: "150ms" }}
            >
              <div className="text-[0.62rem] text-muted-foreground uppercase tracking-[.05em] mb-[.3rem]">{kpi.label}</div>
              <div className="text-[1.15rem] font-semibold font-mono" style={{ color: kpi.color }}>
                {loading ? <span className="text-qds-t3">—</span> : kpi.value}
              </div>
              {kpi.sub && !loading && (
                <div className="text-[0.62rem] font-mono mt-[.15rem]" style={{ color: kpi.color }}>{kpi.sub}</div>
              )}
            </div>
          </FadeIn>
        ))}
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-2 gap-[1rem]">
        {/* Equity curve */}
        <FadeIn delay={0.25}>
          <div className="rounded-lg border bg-card overflow-hidden hover:border-qds-border-hover transition-[border-color]">
            <div className="qds-card-header text-[.75rem] font-semibold">
              <span>权益曲线</span>
              <span className="font-mono text-[.6rem] font-normal text-muted-foreground">30d</span>
            </div>
            <div className="p-[.85rem]">
              {equityLoading ? (
                <div className="flex items-center justify-center h-[220px] text-qds-t3 text-[0.72rem]">加载中...</div>
              ) : equityPoints.length === 0 ? (
                <EmptyState variant="first-use" title="暂无权益数据" className="py-8" />
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={equityPoints} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#36884B" stopOpacity={0.07} />
                        <stop offset="95%" stopColor="#36884B" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(255,255,255,.05)" strokeDasharray="none" />
                    <XAxis dataKey="ts" tick={{ fill: "var(--t3)", fontSize: 9, fontFamily: "IBM Plex Mono" }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtTime(v, rangeHours)} minTickGap={60} />
                    <YAxis tick={{ fill: "var(--t3)", fontSize: 9, fontFamily: "IBM Plex Mono" }} axisLine={false} tickLine={false} tickFormatter={fmtEquity} width={56} />
                    <RechartsTooltip {...CHART_TOOLTIP_PROPS} labelFormatter={(v) => fmtTime(v as string, rangeHours)} formatter={(v: unknown) => [fmtEquity(v as number), "权益"]} />
                    {startEquity != null && <ReferenceLine y={startEquity} stroke="var(--warn)" strokeDasharray="4 4" strokeOpacity={0.4} />}
                    <Area type="monotone" dataKey="equity" stroke="#36884B" strokeWidth={1.5} fill="url(#eqGrad)" animationDuration={800} animationEasing="ease-out" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </FadeIn>

        {/* Realtime PnL */}
        <FadeIn delay={0.3}>
          <div className="rounded-lg border bg-card overflow-hidden hover:border-qds-border-hover transition-[border-color]">
            <div className="qds-card-header text-[.75rem] font-semibold">
              <span>实时 PnL</span>
              <span className="font-mono text-[.6rem] font-normal text-muted-foreground">today</span>
            </div>
            <div className="p-[.85rem]">
              {equityPoints.length === 0 ? (
                <div className="flex items-center justify-center h-[220px]">
                  <div className="text-center">
                    <div className={`text-[2rem] font-bold font-mono tracking-tight ${totalRealizedPnl + totalUnrealizedPnl >= 0 ? "text-qds-success" : "text-destructive"}`}>
                      {fmtPnl(totalRealizedPnl + totalUnrealizedPnl)}
                    </div>
                    <div className="text-[0.62rem] font-mono text-muted-foreground mt-1">已实现 + 未实现</div>
                  </div>
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={equityPoints} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={totalRealizedPnl + totalUnrealizedPnl >= 0 ? "#36884B" : "#FE8181"} stopOpacity={0.07} />
                        <stop offset="95%" stopColor={totalRealizedPnl + totalUnrealizedPnl >= 0 ? "#36884B" : "#FE8181"} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="rgba(255,255,255,.05)" strokeDasharray="none" />
                    <XAxis dataKey="ts" tick={{ fill: "var(--t3)", fontSize: 9, fontFamily: "IBM Plex Mono" }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtTime(v, 24)} minTickGap={60} />
                    <YAxis tick={{ fill: "var(--t3)", fontSize: 9, fontFamily: "IBM Plex Mono" }} axisLine={false} tickLine={false} width={40} />
                    <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                    <Area
                      type="monotone"
                      dataKey="equity"
                      stroke={totalRealizedPnl + totalUnrealizedPnl >= 0 ? "#36884B" : "#FE8181"}
                      strokeWidth={1.5}
                      fill="url(#pnlGrad)"
                      animationDuration={800}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </FadeIn>
      </div>

      {/* Strategy table */}
      <FadeIn delay={0.35}>
        <div>
          <div className="qds-section-label">策略 · 点击查看详情</div>
          <div className="rounded-lg border bg-card overflow-hidden hover:border-qds-border-hover transition-[border-color]">
            {strategyMap.size === 0 ? (
              <EmptyState variant="first-use" title="暂无运行策略" description="启动策略后将在此显示运行状态" className="py-10" />
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[3px] p-0" />
                      <TableHead>策略</TableHead>
                      <TableHead>交易所</TableHead>
                      <TableHead className="text-right">品种</TableHead>
                      <TableHead className="text-right">PnL</TableHead>
                      <TableHead className="text-right">已实现</TableHead>
                      <TableHead className="text-right">未实现</TableHead>
                      <TableHead className="text-right">持仓</TableHead>
                      <TableHead className="text-right">成交</TableHead>
                      <TableHead className="w-8" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Array.from(strategyMap.entries()).map(([tag, data]) => {
                      const totalPnl = data.realizedPnl + data.unrealizedPnl;
                      const hasPnlData = data.realizedPnl !== 0 || data.unrealizedPnl !== 0;
                      const accentColor = !hasPnlData ? "var(--t3)" : totalPnl >= 0 ? "var(--suc)" : "var(--dan)";
                      const pnlColor = totalPnl >= 0 ? "var(--suc)" : "var(--dan)";
                      const realizedColor = data.realizedPnl >= 0 ? "var(--suc)" : "var(--dan)";
                      const unrealizedColor = data.unrealizedPnl >= 0 ? "var(--suc)" : "var(--dan)";
                      // Derive exchange from first position instrument_id e.g. "BTCUSDT-PERP.BINANCE" → "Binance"
                      const instrumentId = data.positions[0]?.instrument_id ?? "";
                      const venuePart = instrumentId.includes(".") ? instrumentId.split(".").pop() ?? "" : "";
                      const exchange = venuePart ? venuePart.charAt(0) + venuePart.slice(1).toLowerCase() : "—";
                      // Derive symbol display (part before the dot)
                      const symbolDisplay = instrumentId.includes(".") ? instrumentId.split(".")[0] : instrumentId || "—";
                      const fillCount = data.positions.reduce((acc, p) => acc + (p.event_count ?? 0), 0);
                      return (
                        <TableRow
                          key={tag}
                          onClick={() => onSelectStrategy(tag)}
                          className="cursor-pointer group"
                        >
                          <TableCell className="w-[3px] p-0">
                            <div className="w-[3px] min-h-[36px] rounded-sm" style={{ background: accentColor }} />
                          </TableCell>
                          <TableCell className="whitespace-nowrap">
                            <span className="font-semibold">{tag}</span>
                            <span className="ml-2 px-1.5 py-0.5 rounded-full text-[0.56rem] font-bold bg-qds-success-dim text-qds-success">Running</span>
                          </TableCell>
                          <TableCell className="whitespace-nowrap">{exchange}</TableCell>
                          <TableCell className="whitespace-nowrap text-right">{symbolDisplay}</TableCell>
                          <TableCell className="whitespace-nowrap text-right font-bold" style={{ color: pnlColor }}>
                            {hasPnlData ? fmtPnl(totalPnl) : "—"}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-right" style={{ color: realizedColor }}>
                            {hasPnlData ? fmtPnl(data.realizedPnl) : "—"}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-right" style={{ color: unrealizedColor }}>
                            {hasPnlData ? fmtPnl(data.unrealizedPnl) : "—"}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-right text-qds-info">{data.positions.length}</TableCell>
                          <TableCell className="whitespace-nowrap text-right">{fillCount || "—"}</TableCell>
                          <TableCell className="whitespace-nowrap text-center text-qds-t3 group-hover:text-primary group-hover:translate-x-[3px] transition-all">→</TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </div>
      </FadeIn>

      {/* Recent fills */}
      <FadeIn delay={0.4}>
        <div className="rounded-lg border bg-card overflow-hidden hover:border-qds-border-hover transition-[border-color]">
          <div className="qds-card-header text-[.75rem] font-semibold">
            <span>最近成交</span>
          </div>
          {fills.length === 0 ? (
            <EmptyState variant="first-use" title="暂无成交记录" className="py-8" />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {["时间", "策略", "品种", "方向", "价格", "数量", "手续费"].map((h) => (
                      <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {fills.slice(0, 15).map((f) => {
                    const isBuy = f.order_side === "BUY" || f.order_side === "buy";
                    return (
                      <TableRow key={f.trade_id}>
                        <TableCell className="font-mono whitespace-nowrap">{fmtFillTime(f.ts_event)}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{f.strategy_id_tag ?? "—"}</TableCell>
                        <TableCell className="font-mono font-semibold whitespace-nowrap">{f.instrument_id}</TableCell>
                        <TableCell className={`font-bold whitespace-nowrap ${isBuy ? "text-qds-success" : "text-destructive"}`}>{isBuy ? "买" : "卖"}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{f.last_px}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{f.last_qty}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{f.commission ?? "—"}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </FadeIn>
    </div>
  );
}
