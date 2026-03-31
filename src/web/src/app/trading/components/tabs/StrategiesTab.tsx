"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion } from "framer-motion";
import { Inbox, Play, Pause, RotateCcw, Square, Loader2, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { apiGet, apiPost } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import type { Position } from "../../page";

interface Props {
  nodeType: "sandbox" | "live";
}

interface StrategyInfo {
  strategy_id: string;
  name: string;
  status: "running" | "paused" | "stopped" | "error";
}

interface PortfolioInfo {
  name: string;
  state: "available" | "starting" | "running" | "paused" | "flattening";
  strategies: string[];
}

const PORTFOLIO_STATE_LABELS: Record<string, string> = {
  available: "可用",
  starting: "启动中",
  running: "运行中",
  paused: "已暂停",
  flattening: "平仓中",
};

const PORTFOLIO_STATE_COLORS: Record<string, string> = {
  available: "var(--muted-foreground)",
  starting: "var(--accent-blue)",
  running: "var(--accent-green)",
  paused: "var(--accent-amber)",
  flattening: "var(--accent-red)",
  error: "var(--accent-red)",
  stopped: "var(--muted-foreground)",
};

const STRATEGY_STATUS_COLORS: Record<string, string> = {
  running: "var(--accent-green)",
  paused: "var(--accent-amber)",
  stopped: "var(--muted-foreground)",
  error: "var(--accent-red)",
};

const STRATEGY_STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  paused: "已暂停",
  stopped: "已停止",
  error: "错误",
};

// ---------------------------------------------------------------------------
// Helper: flatten nested fields object into dot-notation keys
// ---------------------------------------------------------------------------
function flattenFields(
  fields: Record<string, unknown>,
  prefix = ""
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(fields)) {
    const fullKey = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "object" && value !== null && !Array.isArray(value)) {
      Object.assign(
        result,
        flattenFields(value as Record<string, unknown>, fullKey)
      );
    } else {
      result[fullKey] = value;
    }
  }
  return result;
}

function formatSignalValue(v: number): string {
  if (Math.abs(v) >= 10000)
    return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (Math.abs(v) >= 1) return v.toFixed(3);
  if (Math.abs(v) >= 0.001) return v.toFixed(5);
  return v.toExponential(2);
}

// ---------------------------------------------------------------------------
// Sparkline SVG
// ---------------------------------------------------------------------------
function Sparkline({ data }: { data: number[] }) {
  if (data.length < 2) return null;

  const width = 200;
  const height = 24;
  const padding = 2;

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;

  const points = data.map((v, i) => ({
    x: padding + (i / (data.length - 1)) * (width - padding * 2),
    y: padding + (1 - (v - min) / range) * (height - padding * 2),
  }));

  const segments: { d: string; rising: boolean }[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const rising = data[i + 1] >= data[i];
    segments.push({
      d: `M${points[i].x},${points[i].y} L${points[i + 1].x},${points[i + 1].y}`,
      rising,
    });
  }

  return (
    <svg
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
    >
      {segments.map((seg, i) => (
        <path
          key={i}
          d={seg.d}
          stroke={seg.rising ? "var(--accent-green)" : "var(--accent-red)"}
          strokeWidth={1.5}
          fill="none"
          strokeLinecap="round"
        />
      ))}
    </svg>
  );
}

// ---------------------------------------------------------------------------
// SignalRow — single field name + value + sparkline
// ---------------------------------------------------------------------------
function SignalRow({
  name,
  value,
  sparkData,
}: {
  name: string;
  value: number;
  sparkData: number[];
}) {
  return (
    <div className="flex items-center gap-3 py-1 hover:bg-white/[0.02] rounded px-1">
      <span className="text-[11px] text-muted-foreground/60 w-32 truncate shrink-0">
        {name}
      </span>
      <span className="text-[12px] font-mono font-semibold text-foreground w-24 text-right shrink-0">
        {formatSignalValue(value)}
      </span>
      <div className="flex-1 h-6 min-w-[100px]">
        <Sparkline data={sparkData} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// SignalPanel — renders sections + rows from raw nested fields
// ---------------------------------------------------------------------------
function SignalPanel({
  rawFields,
  history,
}: {
  rawFields: Record<string, unknown>;
  history: Record<string, number[]>;
}) {
  if (!rawFields || Object.keys(rawFields).length === 0) {
    return (
      <div className="px-4 py-3 text-[11px] text-muted-foreground/40 text-center">
        暂无信号数据
      </div>
    );
  }

  return (
    <div className="px-4 py-2 space-y-1 border-t border-white/[0.04]">
      {Object.entries(rawFields).map(([key, value]) => {
        if (
          typeof value === "object" &&
          value !== null &&
          !Array.isArray(value)
        ) {
          // Section header + child rows
          return (
            <div key={key}>
              <div className="text-[9px] font-bold tracking-[1.5px] uppercase text-muted-foreground/30 pt-2 pb-1">
                {key}
              </div>
              {Object.entries(value as Record<string, unknown>).map(
                ([childKey, childVal]) =>
                  typeof childVal === "number" ? (
                    <SignalRow
                      key={`${key}.${childKey}`}
                      name={childKey}
                      value={childVal}
                      sparkData={history[`${key}.${childKey}`] ?? []}
                    />
                  ) : null
              )}
            </div>
          );
        }
        if (typeof value === "number") {
          return (
            <SignalRow
              key={key}
              name={key}
              value={value}
              sparkData={history[key] ?? []}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared UI primitives
// ---------------------------------------------------------------------------
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

function SectionHeader({ label }: { label: string }) {
  return (
    <div className="px-4 py-3 border-b border-white/[0.06]">
      <span className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50">
        {label}
      </span>
    </div>
  );
}

function PortfolioButton({
  icon,
  label,
  loading,
  onClick,
  color,
}: {
  icon: React.ReactNode;
  label: string;
  loading: boolean;
  onClick: () => void;
  color: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={loading}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[11px] font-bold transition-opacity hover:opacity-80 disabled:opacity-50"
      style={{
        color,
        backgroundColor: `${color}18`,
        border: `1px solid ${color}30`,
      }}
    >
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : icon}
      {label}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export function StrategiesTab({ nodeType }: Props) {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [portfolios, setPortfolios] = useState<PortfolioInfo[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Signal state
  const [expandedStrategies, setExpandedStrategies] = useState<Set<string>>(
    new Set()
  );
  const [signalHistory, setSignalHistory] = useState<
    Record<string, Record<string, number[]>>
  >({});
  const [signalLatest, setSignalLatest] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [signalRawFields, setSignalRawFields] = useState<
    Record<string, Record<string, unknown>>
  >({});

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fastPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // WS signal.snapshot subscription
  const signalMsg = useWsEvent("signal.snapshot");
  useEffect(() => {
    if (!signalMsg) return;
    const msg = signalMsg as unknown as Record<string, unknown>;
    const data = (msg.data ?? msg) as {
      strategy_id?: string;
      fields?: Record<string, unknown>;
    };
    if (!data?.strategy_id || !data?.fields) return;
    const sid = data.strategy_id;

    // Store raw nested fields for section rendering
    setSignalRawFields((prev) => ({ ...prev, [sid]: data.fields! }));

    // Update latest values (flattened)
    setSignalLatest((prev) => ({
      ...prev,
      [sid]: flattenFields(data.fields!),
    }));

    // Append to history (keep last 30 per field)
    setSignalHistory((prev) => {
      const stratHistory = { ...(prev[sid] ?? {}) };
      const flat = flattenFields(data.fields!);
      for (const [key, val] of Object.entries(flat)) {
        if (typeof val === "number") {
          const arr = [...(stratHistory[key] ?? []), val];
          stratHistory[key] = arr.slice(-30);
        }
      }
      return { ...prev, [sid]: stratHistory };
    });
  }, [signalMsg]);

  const fetchData = useCallback(async () => {
    try {
      const [portfoliosRes, stateRes, posRes] = await Promise.all([
        apiGet<{ portfolios: PortfolioInfo[] | Record<string, unknown> }>(
          "/api/node/portfolios",
          { mode: nodeType }
        ),
        apiGet<{
          strategy_states?: Record<string, string>;
          trading_state?: string;
          paused?: string[];
        }>("/api/node/lifecycle/state", {
          mode: nodeType,
        }),
        apiGet<Position[]>("/api/trading/positions", {
          node_type: nodeType,
        }),
      ]);

      const raw = portfoliosRes?.portfolios;
      const parsedPortfolios: PortfolioInfo[] =
        raw && !Array.isArray(raw)
          ? Object.entries(raw).map(([name, info]) => ({
              name,
              ...(info as Record<string, unknown>),
            } as PortfolioInfo))
          : ((raw as PortfolioInfo[]) ?? []);

      setPortfolios(parsedPortfolios);
      const stateMap = stateRes?.strategy_states ?? {};
      const parsedStrategies: StrategyInfo[] = Object.entries(stateMap).map(
        ([id, status]) => ({
          strategy_id: id,
          name: id,
          status: (status as StrategyInfo["status"]) ?? "stopped",
        })
      );
      setStrategies(parsedStrategies);
      setPositions(posRes ?? []);
    } catch {
      // silent — node may be offline
    } finally {
      setLoading(false);
    }
  }, [nodeType]);

  const startPolling = useCallback(
    (ms: number) => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      intervalRef.current = setInterval(fetchData, ms);
    },
    [fetchData]
  );

  const startFastPollThenRevert = useCallback(() => {
    startPolling(1000);
    if (fastPollTimeoutRef.current) clearTimeout(fastPollTimeoutRef.current);
    fastPollTimeoutRef.current = setTimeout(() => startPolling(5000), 10000);
  }, [startPolling]);

  useEffect(() => {
    setLoading(true);
    fetchData();
    startPolling(5000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (fastPollTimeoutRef.current) clearTimeout(fastPollTimeoutRef.current);
    };
  }, [fetchData, startPolling]);

  // Toggle expand/collapse; fetch history on first expand
  const toggleExpand = useCallback(
    async (strategyId: string) => {
      setExpandedStrategies((prev) => {
        const next = new Set(prev);
        if (next.has(strategyId)) {
          next.delete(strategyId);
        } else {
          next.add(strategyId);
          // Fetch history only on first expand (no existing history)
          if (!signalHistory[strategyId]) {
            apiGet<Record<string, unknown>[]>("/api/trading/signals/history", {
              strategy_id: strategyId,
              node_type: nodeType,
            }).then((data) => {
              if (!data || data.length === 0) return;
              const history: Record<string, number[]> = {};
              for (const snapshot of data) {
                const fields = snapshot.fields as
                  | Record<string, unknown>
                  | undefined;
                if (!fields) continue;
                const flat = flattenFields(fields);
                for (const [key, val] of Object.entries(flat)) {
                  if (typeof val === "number") {
                    if (!history[key]) history[key] = [];
                    history[key].push(val);
                  }
                }
              }
              setSignalHistory((prev) => ({
                ...prev,
                [strategyId]: history,
              }));
              // Set latest + raw from last snapshot
              const lastFields = data[data.length - 1]?.fields as
                | Record<string, unknown>
                | undefined;
              if (lastFields) {
                setSignalLatest((prev) => ({
                  ...prev,
                  [strategyId]: flattenFields(lastFields),
                }));
                setSignalRawFields((prev) => ({
                  ...prev,
                  [strategyId]: lastFields,
                }));
              }
            });
          }
        }
        return next;
      });
    },
    [nodeType, signalHistory]
  );

  const handlePortfolioAction = useCallback(
    async (
      name: string,
      action: "start" | "pause" | "resume" | "flatten-stop"
    ) => {
      if (action === "flatten-stop") {
        const confirmed = window.confirm(
          `确定要平仓并停止 "${name}" 吗？这将关闭所有持仓并移除策略。`
        );
        if (!confirmed) return;
      }

      const ACTION_LABELS: Record<string, string> = {
        start: "启动",
        pause: "暂停",
        resume: "恢复",
        "flatten-stop": "平仓停止",
      };

      const key = `${name}:${action}`;
      setActionLoading(key);
      try {
        await apiPost(`/api/node/portfolio/${action}`, {
          name,
          mode: nodeType,
        });
        toast.success(`组合 "${name}" 已${ACTION_LABELS[action]}`);
        await fetchData();
        startFastPollThenRevert();
      } catch (err) {
        const msg = err instanceof Error ? err.message : "未知错误";
        toast.error(`组合 "${name}" ${ACTION_LABELS[action]}失败: ${msg}`);
      } finally {
        setActionLoading(null);
      }
    },
    [nodeType, fetchData, startFastPollThenRevert]
  );

  // Group realized PnL by strategy_id_tag
  const pnlByTag: Record<string, number> = {};
  for (const pos of positions) {
    const tag = pos.strategy_id_tag;
    if (tag) {
      pnlByTag[tag] = (pnlByTag[tag] ?? 0) + (pos.realized_pnl ?? 0);
    }
  }

  const fmtPnl = (v: number) => {
    const sign = v >= 0 ? "+" : "";
    return `${sign}${v.toFixed(2)}`;
  };

  return (
    <div className="flex flex-col gap-4 p-4 min-h-0">
      {/* Strategy Instances */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard>
          <SectionHeader label="策略实例" />
          {loading ? (
            <div className="p-4 space-y-2">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-10 rounded bg-white/[0.03] animate-pulse"
                />
              ))}
            </div>
          ) : strategies.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2 text-muted-foreground/30">
              <Inbox className="w-7 h-7 opacity-40" />
              <span className="text-xs">暂无运行策略</span>
            </div>
          ) : (
            <div className="divide-y divide-white/[0.04]">
              {strategies.map((s) => {
                const tagPnl = pnlByTag[s.strategy_id] ?? null;
                const statusColor =
                  STRATEGY_STATUS_COLORS[s.status] ??
                  "var(--muted-foreground)";
                const statusLabel =
                  STRATEGY_STATUS_LABELS[s.status] ?? s.status;
                const isExpanded = expandedStrategies.has(s.strategy_id);

                return (
                  <div key={s.strategy_id}>
                    {/* Clickable header row */}
                    <div
                      className="flex items-center justify-between px-4 py-3 hover:bg-white/[0.03] transition-colors cursor-pointer select-none"
                      onClick={() => toggleExpand(s.strategy_id)}
                    >
                      <div className="flex items-center gap-3 min-w-0">
                        <ChevronRight
                          className="w-3.5 h-3.5 shrink-0 text-muted-foreground/40 transition-transform duration-200"
                          style={{
                            transform: isExpanded
                              ? "rotate(90deg)"
                              : "rotate(0deg)",
                          }}
                        />
                        <div
                          className="w-1.5 h-1.5 rounded-full shrink-0"
                          style={{ backgroundColor: statusColor }}
                        />
                        <div className="min-w-0">
                          <div className="text-[12px] font-mono font-semibold text-foreground truncate">
                            {s.name || s.strategy_id}
                          </div>
                          <div className="text-[10px] text-muted-foreground/40 font-mono truncate">
                            {s.strategy_id}
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {tagPnl != null && (
                          <span
                            className="text-[11px] font-mono font-semibold"
                            style={{
                              color:
                                tagPnl >= 0
                                  ? "var(--accent-green)"
                                  : "var(--accent-red)",
                            }}
                          >
                            {fmtPnl(tagPnl)}
                          </span>
                        )}
                        <span
                          className="px-2 py-0.5 rounded text-[9px] font-bold"
                          style={{
                            color: statusColor,
                            backgroundColor: `${statusColor}18`,
                          }}
                        >
                          {statusLabel}
                        </span>
                      </div>
                    </div>

                    {/* Expandable signal panel */}
                    {isExpanded && (
                      <SignalPanel
                        rawFields={signalRawFields[s.strategy_id] ?? {}}
                        history={signalHistory[s.strategy_id] ?? {}}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </GlassCard>
      </motion.div>

      {/* Portfolio Management */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.06, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard>
          <SectionHeader label="组合管理" />
          {loading ? (
            <div className="p-4 space-y-2">
              {[1, 2].map((i) => (
                <div
                  key={i}
                  className="h-16 rounded bg-white/[0.03] animate-pulse"
                />
              ))}
            </div>
          ) : portfolios.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2 text-muted-foreground/30">
              <Inbox className="w-7 h-7 opacity-40" />
              <span className="text-xs">暂无组合</span>
            </div>
          ) : (
            <div className="p-3 flex flex-col gap-2">
              {portfolios.map((p) => {
                const isRunning = p.state === "running";
                const isPaused = p.state === "paused";
                const isAvailable = p.state === "available";
                const isTransitioning =
                  p.state === "starting" || p.state === "flattening";
                const stateColor =
                  PORTFOLIO_STATE_COLORS[p.state] ??
                  "var(--muted-foreground)";
                const stateLabel =
                  PORTFOLIO_STATE_LABELS[p.state] ?? p.state;

                return (
                  <div
                    key={p.name}
                    className="rounded-lg p-3 bg-white/[0.02] border border-white/[0.06] hover:bg-white/[0.04] transition-all"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-[13px] font-semibold text-foreground truncate max-w-[180px]">
                        {p.name}
                      </span>
                      <span
                        className="px-2 py-0.5 rounded text-[9px] font-bold"
                        style={{
                          color: stateColor,
                          backgroundColor: `${stateColor}18`,
                        }}
                      >
                        {stateLabel}
                      </span>
                    </div>

                    {/* Strategy list */}
                    {p.strategies && p.strategies.length > 0 && (
                      <div className="flex flex-wrap gap-1 mb-2">
                        {p.strategies.map((sid) => (
                          <span
                            key={sid}
                            className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-white/[0.04] text-muted-foreground/50"
                          >
                            {sid}
                          </span>
                        ))}
                      </div>
                    )}

                    {/* Action buttons */}
                    <div className="flex items-center gap-1.5 flex-wrap">
                      {isAvailable && (
                        <PortfolioButton
                          icon={<Play className="w-3.5 h-3.5" />}
                          label="启动"
                          loading={actionLoading === `${p.name}:start`}
                          onClick={() =>
                            handlePortfolioAction(p.name, "start")
                          }
                          color="var(--accent-green)"
                        />
                      )}
                      {isRunning && (
                        <PortfolioButton
                          icon={<Pause className="w-3.5 h-3.5" />}
                          label="暂停"
                          loading={actionLoading === `${p.name}:pause`}
                          onClick={() =>
                            handlePortfolioAction(p.name, "pause")
                          }
                          color="var(--accent-amber)"
                        />
                      )}
                      {isPaused && (
                        <PortfolioButton
                          icon={<RotateCcw className="w-3.5 h-3.5" />}
                          label="恢复"
                          loading={actionLoading === `${p.name}:resume`}
                          onClick={() =>
                            handlePortfolioAction(p.name, "resume")
                          }
                          color="var(--accent-blue)"
                        />
                      )}
                      {(isRunning || isPaused) && (
                        <PortfolioButton
                          icon={<Square className="w-3.5 h-3.5" />}
                          label="平仓停止"
                          loading={
                            actionLoading === `${p.name}:flatten-stop`
                          }
                          onClick={() =>
                            handlePortfolioAction(p.name, "flatten-stop")
                          }
                          color="var(--accent-red)"
                        />
                      )}
                      {isTransitioning && (
                        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/50">
                          <Loader2 className="w-3 h-3 animate-spin" />
                          处理中...
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </GlassCard>
      </motion.div>
    </div>
  );
}
