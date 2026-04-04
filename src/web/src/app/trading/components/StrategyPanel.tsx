"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { RefreshCw, Play, Pause, RotateCcw, Square, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { apiGet, apiPost } from "@/lib/api";

interface StrategyInfo {
  name: string;
  state: "available" | "starting" | "running" | "paused" | "flattening";
  strategies: string[];
}

interface LifecycleState {
  strategies: StrategyInfo[];
  registryEntries: StrategyInfo[];
}

interface Props {
  nodeType: "sandbox" | "live";
}

const STRATEGY_STATE_LABELS: Record<string, string> = {
  available: "可用",
  starting: "启动中",
  running: "运行中",
  paused: "已暂停",
  flattening: "平仓中",
};

const STRATEGY_STATE_COLORS: Record<string, string> = {
  available: "var(--muted-foreground)",
  starting: "var(--accent-blue)",
  running: "var(--accent-green)",
  paused: "var(--accent-amber)",
  flattening: "var(--accent-red)",
};

const STRATEGY_STATUS_COLORS: Record<string, string> = {
  running: "var(--accent-green)",
  paused: "var(--accent-amber)",
  stopped: "var(--muted-foreground)",
  error: "var(--accent-red)",
};

const ACTION_LABELS: Record<string, string> = {
  start: "启动",
  pause: "暂停",
  resume: "恢复",
  "flatten-stop": "平仓停止",
};

export function StrategyPanel({ nodeType }: Props) {
  const [data, setData] = useState<LifecycleState>({ strategies: [], registryEntries: [] });
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fastPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [strategiesRes, stateRes] = await Promise.all([
        apiGet<{ strategies: StrategyInfo[] }>("/api/node/strategies", { mode: nodeType }),
        apiGet<{ strategies: StrategyInfo[] }>("/api/node/lifecycle/state", { mode: nodeType }),
      ]);
      const raw = strategiesRes?.strategies;
      const registryEntries: StrategyInfo[] = raw && !Array.isArray(raw)
        ? Object.entries(raw).map(([name, info]) => ({ name, ...(info as Record<string, unknown>) } as StrategyInfo))
        : (raw ?? []);
      setData({
        registryEntries,
        strategies: stateRes?.strategies ?? [],
      });
    } catch (err) {
      toast.error("无法连接到节点 API");
      console.error("fetchData error:", err);
    } finally {
      setLoading(false);
    }
  }, [nodeType]);

  const startPolling = useCallback((intervalMs: number) => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(fetchData, intervalMs);
  }, [fetchData]);

  const startFastPollThenRevert = useCallback(() => {
    // Switch to 1s polling for 10 seconds, then revert to 5s
    startPolling(1000);
    if (fastPollTimeoutRef.current) clearTimeout(fastPollTimeoutRef.current);
    fastPollTimeoutRef.current = setTimeout(() => {
      startPolling(5000);
    }, 10000);
  }, [startPolling]);

  useEffect(() => {
    fetchData();
    startPolling(5000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (fastPollTimeoutRef.current) clearTimeout(fastPollTimeoutRef.current);
    };
  }, [fetchData, startPolling]);

  const handleStrategyAction = useCallback(
    async (strategyName: string, action: "start" | "pause" | "resume" | "flatten-stop") => {
      // Confirmation for dangerous action
      if (action === "flatten-stop") {
        const confirmed = window.confirm(
          `确定要平仓并停止 "${strategyName}" 吗？这将关闭所有持仓并移除策略。`
        );
        if (!confirmed) return;
      }

      const actionLabel = ACTION_LABELS[action];
      const key = `${strategyName}:${action}`;
      setActionLoading(key);
      try {
        await apiPost(`/api/node/strategy/${action}`, { name: strategyName, mode: nodeType });
        toast.success(`策略 "${strategyName}" 已${actionLabel}`);
        await fetchData();
        startFastPollThenRevert();
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        toast.error(`策略 "${strategyName}" ${actionLabel}失败: ${error.message || "未知错误"}`);
      } finally {
        setActionLoading(null);
      }
    },
    [nodeType, fetchData, startFastPollThenRevert]
  );

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
          策略 / 组合
        </span>
        <button
          onClick={fetchData}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="刷新"
        >
          <RefreshCw className="w-3 h-3" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Strategies section */}
        <div className="px-4 pt-3 pb-1">
          <span className="text-[9px] font-bold tracking-[0.5px] text-muted-foreground uppercase">
            策略实例
          </span>
        </div>

        {loading ? (
          <div className="px-4 py-2 space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-7 rounded bg-popover animate-pulse" />
            ))}
          </div>
        ) : data.strategies.length === 0 ? (
          <div className="px-4 py-4 text-[11px] text-muted-foreground">暂无运行策略</div>
        ) : (
          <div className="px-3 pb-2 space-y-0.5">
            {data.strategies.map((s) => (
              <div
                key={s.name}
                className="flex items-center justify-between px-2 py-1.5 rounded hover:bg-popover transition-colors"
              >
                <span className="text-[11px] font-mono text-foreground truncate max-w-[140px]">
                  {s.name}
                </span>
                <span
                  className="px-2 py-0.5 rounded text-[9px] font-bold"
                  style={{
                    color: STRATEGY_STATUS_COLORS[s.state] ?? "var(--muted-foreground)",
                    backgroundColor: `${STRATEGY_STATUS_COLORS[s.state] ?? "var(--muted-foreground)"}18`,
                  }}
                >
                  {s.state === "running" ? "运行" : s.state === "paused" ? "暂停" : s.state === "available" ? "停止" : s.state}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Divider */}
        <div className="mx-4 border-t border-border my-2" />

        {/* Registry entries section */}
        <div className="px-4 pb-1">
          <span className="text-[9px] font-bold tracking-[0.5px] text-muted-foreground uppercase">
            组合管理
          </span>
        </div>

        {loading ? (
          <div className="px-4 py-2 space-y-2">
            {[1, 2].map((i) => (
              <div key={i} className="h-14 rounded bg-popover animate-pulse" />
            ))}
          </div>
        ) : data.registryEntries.length === 0 ? (
          <div className="px-4 py-4 text-[11px] text-muted-foreground">暂无组合</div>
        ) : (
          <div className="px-3 pb-3 space-y-1">
            {data.registryEntries.map((p) => {
              const isRunning = p.state === "running";
              const isPaused = p.state === "paused";
              const isAvailable = p.state === "available";
              const isTransitioning = p.state === "starting" || p.state === "flattening";

              return (
                <div
                  key={p.name}
                  className="rounded p-3 bg-popover border border-border"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[12px] font-semibold text-foreground truncate max-w-[120px]">
                      {p.name}
                    </span>
                    <span
                      className="px-1.5 py-0.5 rounded text-[9px] font-bold"
                      style={{
                        color: STRATEGY_STATE_COLORS[p.state] ?? "var(--muted-foreground)",
                        backgroundColor: `${STRATEGY_STATE_COLORS[p.state] ?? "var(--muted-foreground)"}18`,
                      }}
                    >
                      {STRATEGY_STATE_LABELS[p.state] ?? p.state}
                    </span>
                  </div>

                  {/* Action buttons */}
                  <div className="flex items-center gap-1.5">
                    {isAvailable && (
                      <StrategyButton
                        icon={<Play className="w-3.5 h-3.5" />}
                        label="启动"
                        loading={actionLoading === `${p.name}:start`}
                        onClick={() => handleStrategyAction(p.name, "start")}
                        color="var(--accent-green)"
                      />
                    )}
                    {isRunning && (
                      <StrategyButton
                        icon={<Pause className="w-3.5 h-3.5" />}
                        label="暂停"
                        loading={actionLoading === `${p.name}:pause`}
                        onClick={() => handleStrategyAction(p.name, "pause")}
                        color="var(--accent-amber)"
                      />
                    )}
                    {isPaused && (
                      <StrategyButton
                        icon={<RotateCcw className="w-3.5 h-3.5" />}
                        label="恢复"
                        loading={actionLoading === `${p.name}:resume`}
                        onClick={() => handleStrategyAction(p.name, "resume")}
                        color="var(--accent-blue)"
                      />
                    )}
                    {(isRunning || isPaused) && (
                      <StrategyButton
                        icon={<Square className="w-3.5 h-3.5" />}
                        label="平仓停止"
                        loading={actionLoading === `${p.name}:flatten-stop`}
                        onClick={() => handleStrategyAction(p.name, "flatten-stop")}
                        color="var(--accent-red)"
                      />
                    )}
                    {isTransitioning && (
                      <span className="text-[9px] text-muted-foreground">处理中...</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function StrategyButton({
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
      {loading ? (
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
      ) : (
        icon
      )}
      {label}
    </button>
  );
}
