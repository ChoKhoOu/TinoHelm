"use client";

import { useState, useEffect, useCallback } from "react";
import { RefreshCw, Play, Pause, RotateCcw, Square } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";

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

interface LifecycleState {
  strategies: StrategyInfo[];
  portfolios: PortfolioInfo[];
}

interface Props {
  nodeType: "sandbox" | "live";
}

const PORTFOLIO_STATE_LABELS: Record<string, string> = {
  available: "可用",
  starting: "启动中",
  running: "运行中",
  paused: "已暂停",
  flattening: "平仓中",
};

const PORTFOLIO_STATE_COLORS: Record<string, string> = {
  available: "var(--text-muted)",
  starting: "var(--accent-blue)",
  running: "var(--accent-green)",
  paused: "var(--accent-amber)",
  flattening: "var(--accent-red)",
};

const STRATEGY_STATUS_COLORS: Record<string, string> = {
  running: "var(--accent-green)",
  paused: "var(--accent-amber)",
  stopped: "var(--text-muted)",
  error: "var(--accent-red)",
};

export function StrategyPanel({ nodeType }: Props) {
  const [data, setData] = useState<LifecycleState>({ strategies: [], portfolios: [] });
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [portfoliosRes, stateRes] = await Promise.all([
        apiGet<{ portfolios: PortfolioInfo[] }>("/api/node/portfolios", { mode: nodeType }),
        apiGet<{ strategies: StrategyInfo[] }>("/api/node/lifecycle/state", { mode: nodeType }),
      ]);
      setData({
        portfolios: portfoliosRes?.portfolios ?? [],
        strategies: stateRes?.strategies ?? [],
      });
    } catch {
      // silent — show stale data
    } finally {
      setLoading(false);
    }
  }, [nodeType]);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, 5000);
    return () => clearInterval(id);
  }, [fetchData]);

  const handlePortfolioAction = useCallback(
    async (portfolioName: string, action: "start" | "pause" | "resume" | "flatten-stop") => {
      const key = `${portfolioName}:${action}`;
      setActionLoading(key);
      try {
        await apiPost(`/api/node/portfolio/${action}`, { name: portfolioName, mode: nodeType });
        await fetchData();
      } catch {
        // silent
      } finally {
        setActionLoading(null);
      }
    },
    [nodeType, fetchData]
  );

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-gray)]">
        <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
          策略 / 组合
        </span>
        <button
          onClick={fetchData}
          className="text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
          title="刷新"
        >
          <RefreshCw className="w-3 h-3" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Strategies section */}
        <div className="px-4 pt-3 pb-1">
          <span className="text-[9px] font-bold tracking-[0.5px] text-[var(--text-muted)] uppercase">
            策略实例
          </span>
        </div>

        {loading ? (
          <div className="px-4 py-2 space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-7 rounded bg-[var(--bg-elevated)] animate-pulse" />
            ))}
          </div>
        ) : data.strategies.length === 0 ? (
          <div className="px-4 py-4 text-[11px] text-[var(--text-muted)]">暂无运行策略</div>
        ) : (
          <div className="px-3 pb-2 space-y-0.5">
            {data.strategies.map((s) => (
              <div
                key={s.strategy_id}
                className="flex items-center justify-between px-2 py-1.5 rounded hover:bg-[var(--bg-elevated)] transition-colors"
              >
                <span className="text-[11px] font-mono text-[var(--text-primary)] truncate max-w-[140px]">
                  {s.name || s.strategy_id}
                </span>
                <span
                  className="px-2 py-0.5 rounded text-[9px] font-bold"
                  style={{
                    color: STRATEGY_STATUS_COLORS[s.status] ?? "var(--text-muted)",
                    backgroundColor: `${STRATEGY_STATUS_COLORS[s.status] ?? "var(--text-muted)"}18`,
                  }}
                >
                  {s.status === "running" ? "运行" : s.status === "paused" ? "暂停" : s.status === "error" ? "错误" : "停止"}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Divider */}
        <div className="mx-4 border-t border-[var(--border-gray)] my-2" />

        {/* Portfolios section */}
        <div className="px-4 pb-1">
          <span className="text-[9px] font-bold tracking-[0.5px] text-[var(--text-muted)] uppercase">
            组合管理
          </span>
        </div>

        {loading ? (
          <div className="px-4 py-2 space-y-2">
            {[1, 2].map((i) => (
              <div key={i} className="h-14 rounded bg-[var(--bg-elevated)] animate-pulse" />
            ))}
          </div>
        ) : data.portfolios.length === 0 ? (
          <div className="px-4 py-4 text-[11px] text-[var(--text-muted)]">暂无组合</div>
        ) : (
          <div className="px-3 pb-3 space-y-1">
            {data.portfolios.map((p) => {
              const isRunning = p.state === "running";
              const isPaused = p.state === "paused";
              const isAvailable = p.state === "available";
              const isTransitioning = p.state === "starting" || p.state === "flattening";

              return (
                <div
                  key={p.name}
                  className="rounded p-2 bg-[var(--bg-elevated)] border border-[var(--border-gray)]"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[11px] font-semibold text-[var(--text-primary)] truncate max-w-[120px]">
                      {p.name}
                    </span>
                    <span
                      className="px-1.5 py-0.5 rounded text-[9px] font-bold"
                      style={{
                        color: PORTFOLIO_STATE_COLORS[p.state] ?? "var(--text-muted)",
                        backgroundColor: `${PORTFOLIO_STATE_COLORS[p.state] ?? "var(--text-muted)"}18`,
                      }}
                    >
                      {PORTFOLIO_STATE_LABELS[p.state] ?? p.state}
                    </span>
                  </div>

                  {/* Action buttons */}
                  <div className="flex items-center gap-1">
                    {isAvailable && (
                      <PortfolioButton
                        icon={<Play className="w-2.5 h-2.5" />}
                        label="启动"
                        loading={actionLoading === `${p.name}:start`}
                        onClick={() => handlePortfolioAction(p.name, "start")}
                        color="var(--accent-green)"
                      />
                    )}
                    {isRunning && (
                      <PortfolioButton
                        icon={<Pause className="w-2.5 h-2.5" />}
                        label="暂停"
                        loading={actionLoading === `${p.name}:pause`}
                        onClick={() => handlePortfolioAction(p.name, "pause")}
                        color="var(--accent-amber)"
                      />
                    )}
                    {isPaused && (
                      <PortfolioButton
                        icon={<RotateCcw className="w-2.5 h-2.5" />}
                        label="恢复"
                        loading={actionLoading === `${p.name}:resume`}
                        onClick={() => handlePortfolioAction(p.name, "resume")}
                        color="var(--accent-blue)"
                      />
                    )}
                    {(isRunning || isPaused) && (
                      <PortfolioButton
                        icon={<Square className="w-2.5 h-2.5" />}
                        label="平仓停止"
                        loading={actionLoading === `${p.name}:flatten-stop`}
                        onClick={() => handlePortfolioAction(p.name, "flatten-stop")}
                        color="var(--accent-red)"
                      />
                    )}
                    {isTransitioning && (
                      <span className="text-[9px] text-[var(--text-muted)]">处理中...</span>
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
      className="flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-bold transition-opacity hover:opacity-80 disabled:opacity-50"
      style={{
        color,
        backgroundColor: `${color}18`,
        border: `1px solid ${color}30`,
      }}
    >
      {loading ? (
        <RefreshCw className="w-2.5 h-2.5 animate-spin" />
      ) : (
        icon
      )}
      {label}
    </button>
  );
}
