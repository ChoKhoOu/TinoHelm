"use client";

import { useState, useEffect, useCallback } from "react";
import { ArrowLeft, Settings2 } from "lucide-react";
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
import { apiGet, apiPost } from "@/lib/api";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { CHART_TOOLTIP_PROPS, CHART_GRID_STYLE } from "@/lib/chartTheme";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { TickFlash } from "@/components/TickFlash";
import { EmptyState } from "@/components/EmptyState";
import type { Position, Fill } from "../page";

interface Props {
  strategyId: string;
  nodeType: "sandbox" | "live";
  positions: Position[];
  fills: Fill[];
  onBack: () => void;
}

interface EquityPoint {
  ts: string;
  equity: number;
}

interface StrategyParamsResponse {
  name: string;
  config_params: Array<{ name: string; type: string; default: string | number | boolean | null }>;
  optimize_ranges: Record<string, unknown>;
}


function fmtPnl(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}`;
}

function fmtEquity(v: number): string {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `$${(v / 1_000).toFixed(2)}K`;
  return `$${v.toFixed(2)}`;
}

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return ts;
  }
}

// QDS Toggle component (34x18px)
function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="relative inline-flex items-center flex-shrink-0 cursor-pointer transition-colors"
      style={{
        width: 34,
        height: 18,
        borderRadius: 9,
        background: checked ? "var(--suc)" : "var(--bg-t)",
        border: "1px solid var(--bd)",
        transitionDuration: "var(--dur)",
      }}
    >
      <span
        className="inline-block rounded-full bg-white transition-transform"
        style={{
          width: 12,
          height: 12,
          transform: checked ? "translateX(17px)" : "translateX(2px)",
          transitionDuration: "var(--dur)",
          boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
        }}
      />
    </button>
  );
}


export function StrategyDetailPanel({ strategyId, nodeType, positions, fills, onBack }: Props) {
  const [equityPoints, setEquityPoints] = useState<EquityPoint[]>([]);
  const [equityLoading, setEquityLoading] = useState(true);
  const [isRunning, setIsRunning] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [strategyConfig, setStrategyConfig] = useState<Record<string, string> | null>(null);

  const handleToggle = useCallback(async (checked: boolean) => {
    setActionLoading(true);
    try {
      const action = checked ? "resume" : "pause";
      await apiPost(`/api/node/strategy/${action}`, { name: strategyId, mode: nodeType });
      setIsRunning(checked);
    } catch { /* inline error handled by visual state */ }
    finally { setActionLoading(false); }
  }, [strategyId, nodeType]);

  const handleFlattenStop = useCallback(async () => {
    setActionLoading(true);
    try {
      await apiPost("/api/node/strategy/flatten-stop", { name: strategyId, mode: nodeType });
      setIsRunning(false);
    } catch { /* inline error handled by visual state */ }
    finally { setActionLoading(false); }
  }, [strategyId, nodeType]);

  // Filter positions/fills for this strategy
  const stratPositions = positions.filter((p) => p.strategy_id_tag === strategyId);
  const stratFills = fills.filter((f) => f.strategy_id_tag === strategyId);

  // Load equity data
  useEffect(() => {
    let cancelled = false;
    setEquityLoading(true);
    setEquityPoints([]);
    apiGet<{ points: EquityPoint[] } | EquityPoint[]>("/api/trading/equity", {
      node_type: nodeType,
      strategy_id: strategyId,
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
  }, [nodeType, strategyId]);

  // Load strategy config params
  useEffect(() => {
    let cancelled = false;
    apiGet<StrategyParamsResponse>(`/api/strategies/${encodeURIComponent(strategyId)}/params`)
      .then((res) => {
        if (cancelled) return;
        if (Array.isArray(res?.config_params)) {
          const kvMap: Record<string, string> = {};
          for (const p of res.config_params) {
            kvMap[p.name] = String(p.default ?? "");
          }
          setStrategyConfig(kvMap);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [strategyId]);

  // WS equity append
  const equityMsg = useWsEvent("equity.snapshot");
  useEffect(() => {
    if (!equityMsg) return;
    const d = (equityMsg.data ?? equityMsg) as { node_type?: string; strategy_id?: string; ts?: string; equity?: number };
    if (d?.node_type && d.node_type !== nodeType) return;
    if (d?.strategy_id && d.strategy_id !== strategyId) return;
    if (d?.ts && d?.equity != null) {
      setEquityPoints((prev) => [...prev, { ts: d.ts!, equity: d.equity! }].slice(-1000));
    }
  }, [equityMsg, nodeType, strategyId]);

  const totalRealizedPnl = stratPositions.reduce((s, p) => s + (p.realized_pnl ?? 0), 0);
  const totalUnrealizedPnl = stratPositions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  const totalPnl = totalRealizedPnl + totalUnrealizedPnl;
  const latestEquity = equityPoints.length > 0 ? equityPoints[equityPoints.length - 1].equity : null;
  const startEquity = equityPoints.length > 0 ? equityPoints[0].equity : null;

  const kpis = [
    { label: "策略 PnL", value: fmtPnl(totalPnl), color: totalPnl >= 0 ? "var(--suc)" : "var(--dan)" },
    { label: "已实现", value: fmtPnl(totalRealizedPnl), color: totalRealizedPnl >= 0 ? "var(--suc)" : "var(--dan)" },
    { label: "未实现", value: fmtPnl(totalUnrealizedPnl), color: totalUnrealizedPnl >= 0 ? "var(--suc)" : "var(--dan)" },
    { label: "持仓数", value: String(stratPositions.length), color: "var(--info)" },
    { label: "成交量", value: String(stratFills.length), color: "var(--t0)" },
  ];

  return (
    <div
      className="flex flex-col gap-5 p-5 min-h-0 overflow-y-auto"
      style={{ animation: "qds-slide-in 350ms var(--eo) both" }}
    >
      {/* Header */}
      <div className="rounded-lg border bg-card px-4 py-3">
        {/* Row 1: Back button */}
        <div className="mb-2">
          <button
            onClick={onBack}
            className="flex items-center gap-1.5 text-[0.78rem] font-semibold text-muted-foreground hover:text-foreground transition-colors"
            style={{ transitionDuration: "var(--dur)" }}
          >
            <ArrowLeft className="size-3.5" />
            返回总览
          </button>
        </div>

        {/* Row 2+3: Name / subtitle / actions */}
        <div className="flex items-end justify-between gap-4">
          <div>
            <h2 className="text-[1rem] font-bold font-mono text-foreground leading-tight">{strategyId}</h2>
            <span className="text-[0.75rem] text-muted-foreground">
              {nodeType === "sandbox" ? "Sandbox" : "Binance"} · {stratPositions[0]?.instrument_id ?? "—"} · {nodeType === "sandbox" ? "Paper" : "Live"}
            </span>
          </div>

          {/* Actions row */}
          <div className="flex items-center gap-2 flex-shrink-0">
            {/* Running badge */}
            <span
              className="px-2 py-0.5 rounded-full text-[0.56rem] font-bold"
              style={{
                background: isRunning ? "var(--suc-d, rgba(38,217,127,0.15))" : "var(--bg-t)",
                color: isRunning ? "var(--suc)" : "var(--t2)",
              }}
            >
              {isRunning ? "Running" : "Paused"}
            </span>

            {/* Toggle */}
            <Toggle checked={isRunning} onChange={handleToggle} />

            {/* Config button */}
            <button
              className="flex items-center gap-1.5 rounded-sm border text-qds-t1 text-[0.72rem] px-3 py-1.5 hover:bg-secondary transition-colors"
              style={{ transitionDuration: "var(--dur)" }}
            >
              <Settings2 className="size-3" />
              配置
            </button>

            {/* Stop button */}
            <button
              onClick={handleFlattenStop}
              disabled={actionLoading}
              className="rounded border text-[0.72rem] px-3 py-1.5 hover:bg-qds-danger-dim transition-colors disabled:opacity-50"
              style={{
                borderColor: "var(--dan)",
                color: "var(--dan)",
                transitionDuration: "var(--dur)",
              }}
            >
              停止
            </button>
          </div>
        </div>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-5 gap-3">
        {kpis.map((kpi, i) => (
          <div
            key={kpi.label}
            className="rounded-lg border bg-card p-3 hover:bg-secondary transition-colors"
            style={{ transitionDuration: "var(--dur)", animationDelay: `${i * 50}ms` }}
          >
            <div className="qds-stat-label">{kpi.label}</div>
            <div className="text-[1.1rem] font-bold font-mono" style={{ color: kpi.color }}>{kpi.value}</div>
          </div>
        ))}
      </div>

      {/* Two-column chart layout */}
      <div className="grid grid-cols-2 gap-3">
        {/* Left: Equity curve */}
        <div className="rounded-lg border bg-card p-4">
          <div className="qds-section-label mb-3">策略权益曲线</div>
          {equityLoading ? (
            <div className="flex items-center justify-center h-[200px] text-qds-t3 text-[0.72rem]">加载中...</div>
          ) : equityPoints.length === 0 ? (
            <EmptyState variant="first-use" title="暂无权益数据" className="py-10" />
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={equityPoints} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id="sdpGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="var(--info)" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="var(--info)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid {...CHART_GRID_STYLE} />
                <XAxis dataKey="ts" tick={{ fill: "var(--t3)", fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtTime(v)} minTickGap={60} />
                <YAxis tick={{ fill: "var(--t3)", fontSize: 9 }} axisLine={false} tickLine={false} tickFormatter={fmtEquity} width={56} />
                <RechartsTooltip {...CHART_TOOLTIP_PROPS} formatter={(v: unknown) => [fmtEquity(v as number), "权益"]} />
                {startEquity != null && <ReferenceLine y={startEquity} stroke="var(--warn)" strokeDasharray="4 4" strokeOpacity={0.4} />}
                <Area type="monotone" dataKey="equity" stroke="var(--info)" strokeWidth={1.5} fill="url(#sdpGrad)" animationDuration={1500} animationEasing="ease-out" dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Right: Today PnL */}
        <div className="rounded-lg border bg-card p-4">
          <div className="qds-section-label mb-3">今日 PnL</div>
          <div className="flex items-center justify-center h-[200px]">
            <div className="text-center">
              <div
                className="text-[2rem] font-bold font-mono leading-none"
                style={{ color: totalPnl >= 0 ? "var(--suc)" : "var(--dan)" }}
              >
                {fmtPnl(totalPnl)}
              </div>
              <div className="text-[0.68rem] text-muted-foreground mt-2">已实现 + 未实现</div>
              {latestEquity != null && (
                <div className="text-[0.62rem] text-qds-t3 mt-1">
                  权益 {fmtEquity(latestEquity)}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Positions table */}
      <div>
        <div className="qds-section-label">持仓</div>
        <div className="rounded-lg border bg-card overflow-hidden">
          {stratPositions.length === 0 ? (
            <EmptyState variant="first-use" title="暂无持仓" className="py-8" />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {["品种", "方向", "数量", "开仓均价", "未实现PnL", "时长", ""].map((h, idx) => (
                      <TableHead key={idx} className="whitespace-nowrap">{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stratPositions.map((p) => {
                    const isLong = p.side === "LONG";
                    const pnlVal = p.unrealized_pnl ?? 0;
                    return (
                      <TableRow key={p.position_id}>
                        <TableCell className="font-mono font-semibold whitespace-nowrap">{p.instrument_id}</TableCell>
                        <TableCell className={`font-bold whitespace-nowrap ${isLong ? "text-qds-success" : "text-destructive"}`}>{isLong ? "多" : "空"}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{p.quantity}</TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{p.avg_px_open ?? "—"}</TableCell>
                        <TableCell className="whitespace-nowrap">
                          <TickFlash value={pnlVal}>
                            <span className={`font-mono font-semibold ${pnlVal >= 0 ? "text-qds-success" : "text-destructive"}`}>{fmtPnl(pnlVal)}</span>
                          </TickFlash>
                        </TableCell>
                        <TableCell className="font-mono whitespace-nowrap">{p.duration ?? "—"}</TableCell>
                        <TableCell className="whitespace-nowrap">
                          <button
                            onClick={() => apiPost("/api/node/lifecycle", { action: "flatten", mode: nodeType, strategy_id: strategyId })}
                            className="text-[0.62rem] rounded px-2 py-1 border border-destructive text-destructive hover:bg-destructive/10 transition-colors"
                            style={{ transitionDuration: "var(--dur)" }}
                          >
                            平仓
                          </button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      </div>

      {/* Recent fills */}
      <div>
        <div className="qds-section-label">最近成交</div>
        <div className="rounded-lg border bg-card overflow-hidden">
          {stratFills.length === 0 ? (
            <EmptyState variant="first-use" title="暂无成交" className="py-8" />
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {["时间", "品种", "方向", "价格", "数量", "手续费"].map((h) => (
                      <TableHead key={h} className="whitespace-nowrap">{h}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {stratFills.slice(0, 20).map((f) => {
                    const isBuy = f.order_side === "BUY" || f.order_side === "buy";
                    return (
                      <TableRow key={f.trade_id}>
                        <TableCell className="font-mono whitespace-nowrap">{fmtTime(f.ts_event)}</TableCell>
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
      </div>

      {/* Config grid — 运行参数 */}
      <div>
        <div className="qds-section-label">运行参数</div>
        <div className="rounded-lg border bg-card p-4">
          {strategyConfig == null ? (
            <div className="text-[0.72rem] text-qds-t3 text-center py-4">暂无参数配置</div>
          ) : Object.keys(strategyConfig).length === 0 ? (
            <div className="text-[0.72rem] text-qds-t3 text-center py-4">暂无参数配置</div>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-2 font-mono text-[0.72rem]">
              {Object.entries(strategyConfig).map(([key, value]) => (
                <div key={key} className="flex justify-between px-3 py-2 bg-input rounded">
                  <span className="text-muted-foreground truncate mr-2">{key}</span>
                  <span className="text-foreground font-semibold flex-shrink-0">{String(value)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
