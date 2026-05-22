"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { apiGet, apiPost } from "@/lib/api";
import { FadeIn } from "@/components/motion/FadeIn";
import { EmptyState } from "@/components/EmptyState";
import { ConfirmModal } from "@/components/ConfirmModal";
import type { Position } from "../../page";

interface Props {
  nodeType: "sandbox" | "live";
}

interface RuntimeEntry {
  name: string;
  state: "available" | "starting" | "running" | "paused" | "flattening";
  strategy_ids: string[];
  symbols: string[];
  interval: string;
}

const STATE_COLORS: Record<string, string> = {
  available: "var(--t2)",
  starting: "var(--info)",
  running: "var(--suc)",
  paused: "var(--warn)",
  flattening: "var(--dan)",
};

const STATE_LABELS: Record<string, string> = {
  available: "可用",
  starting: "启动中",
  running: "运行中",
  paused: "已暂停",
  flattening: "平仓中",
};

function Toggle({
  on,
  loading,
  onClick,
}: {
  on: boolean;
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      disabled={loading}
      className="relative w-[34px] h-[18px] rounded-[9px] border transition-all duration-200 shrink-0"
      style={{
        background: on ? "var(--suc)" : "var(--bg-t)",
        borderColor: on ? "var(--suc)" : "var(--bd)",
        opacity: loading ? 0.5 : 1,
      }}
    >
      <span
        className="absolute top-[2px] w-3 h-3 rounded-full transition-all duration-200"
        style={{
          left: on ? "18px" : "2px",
          background: on ? "#fff" : "var(--t1)",
        }}
      />
    </button>
  );
}

function deriveExchange(_symbols: string[], positions: Position[]): string {
  // Try to derive exchange from instrument_id in positions
  for (const pos of positions) {
    const parts = pos.instrument_id?.split(".");
    if (parts && parts.length > 1) {
      const exchange = parts[parts.length - 1];
      // Convert to display name
      const exchangeMap: Record<string, string> = {
        BINANCE: "Binance",
        BYBIT: "Bybit",
        OKX: "OKX",
        DERIBIT: "Deribit",
      };
      return exchangeMap[exchange] ?? exchange;
    }
  }
  return "Binance";
}

export function StrategiesTab({ nodeType }: Props) {
  const [entries, setEntries] = useState<RuntimeEntry[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [hoveredEntry, setHoveredEntry] = useState<string | null>(null);
  const [confirmFlatten, setConfirmFlatten] = useState<string | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const fastPollTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [strategiesRes, posRes] = await Promise.all([
        apiGet<{ strategies: Record<string, unknown> }>(
          "/api/node/strategies",
          { mode: nodeType }
        ),
        apiGet<Position[]>("/api/trading/positions", { node_type: nodeType }),
      ]);

      const raw = strategiesRes?.strategies;
      const parsed: RuntimeEntry[] =
        raw && !Array.isArray(raw)
          ? Object.entries(raw).map(([name, info]) => {
              const entry = info as Record<string, unknown>;
              return {
                name,
                state: (entry.state as RuntimeEntry["state"]) ?? "available",
                strategy_ids: (entry.strategy_ids as string[]) ?? [],
                symbols: (entry.symbols as string[]) ?? [],
                interval: (entry.interval as string) ?? "",
              };
            })
          : [];

      setEntries(parsed);
      setPositions(posRes ?? []);
    } catch {
      // silent
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

  const handleAction = useCallback(
    async (
      name: string,
      action: "start" | "pause" | "resume" | "flatten-stop"
    ) => {
      const ACTION_LABELS: Record<string, string> = {
        start: "启动",
        pause: "暂停",
        resume: "恢复",
        "flatten-stop": "平仓停止",
      };
      setActionLoading(name);
      try {
        await apiPost(`/api/node/strategy/${action}`, { name, mode: nodeType });
        toast.success(`策略 "${name}" 已${ACTION_LABELS[action]}`);
        await fetchData();
        startFastPollThenRevert();
      } catch (err) {
        const msg = err instanceof Error ? err.message : "未知错误";
        toast.error(`策略 "${name}" ${ACTION_LABELS[action]}失败: ${msg}`);
      } finally {
        setActionLoading(null);
      }
    },
    [nodeType, fetchData, startFastPollThenRevert]
  );

  const handleToggle = useCallback(
    (entry: RuntimeEntry) => {
      if (entry.state === "available") {
        handleAction(entry.name, "start");
      } else if (entry.state === "running") {
        handleAction(entry.name, "pause");
      } else if (entry.state === "paused") {
        handleAction(entry.name, "resume");
      }
    },
    [handleAction]
  );

  // Compute PnL per entry: match strategy_ids against position strategy_id_tag
  function getPnl(entry: RuntimeEntry): number | null {
    const idTags = new Set(
      entry.strategy_ids.map((sid) => {
        // strategy_ids like "BtcMultiFactor-000" → tag is last 3 chars "000"
        const match = sid.match(/-([0-9A-Fa-f]{3})$/);
        return match ? match[1] : sid;
      })
    );
    const matching = positions.filter(
      (p) => p.strategy_id_tag && idTags.has(p.strategy_id_tag)
    );
    if (matching.length === 0) return null;
    return matching.reduce(
      (sum, p) => sum + (p.realized_pnl ?? 0) + (p.unrealized_pnl ?? 0),
      0
    );
  }

  function getPositionCount(entry: RuntimeEntry): number {
    const idTags = new Set(
      entry.strategy_ids.map((sid) => {
        const match = sid.match(/-([0-9A-Fa-f]{3})$/);
        return match ? match[1] : sid;
      })
    );
    return positions.filter(
      (p) => p.strategy_id_tag && idTags.has(p.strategy_id_tag)
    ).length;
  }

  const fmtPnl = (v: number) =>
    `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;

  return (
    <div className="p-5 min-h-0">
      <FadeIn>
        <div className="rounded-lg border bg-card overflow-hidden">
          {loading ? (
            <div className="p-4 space-y-2">
              {[1, 2, 3].map((i) => (
                <div
                  key={i}
                  className="h-[58px] rounded bg-secondary animate-pulse"
                />
              ))}
            </div>
          ) : entries.length === 0 ? (
            <EmptyState
              variant="first-use"
              title="暂无策略"
              description="在 ~/.tino/strategies/ 中添加策略文件"
              className="py-12"
            />
          ) : (
            <div className="divide-y divide-[var(--bd)]">
              {entries.map((entry) => {
                const isTransitioning =
                  entry.state === "starting" || entry.state === "flattening";
                const isOn =
                  entry.state === "running" || entry.state === "paused";
                const canStop =
                  entry.state === "running" || entry.state === "paused";
                const stateColor =
                  STATE_COLORS[entry.state] ?? "var(--t2)";
                const stateLabel = STATE_LABELS[entry.state] ?? entry.state;
                const pnl = getPnl(entry);
                const posCount = getPositionCount(entry);
                const exchange = deriveExchange(entry.symbols, positions);
                const symbolDisplay =
                  entry.symbols.length > 0
                    ? entry.symbols.join(", ")
                    : "—";
                const isLoading = actionLoading === entry.name;
                const isHovered = hoveredEntry === entry.name;

                return (
                  <div
                    key={entry.name}
                    className="flex items-center gap-3.5 px-5 py-3.5 hover:bg-secondary transition-colors"
                    style={{ transitionDuration: "var(--dur)" }}
                    onMouseEnter={() => setHoveredEntry(entry.name)}
                    onMouseLeave={() => setHoveredEntry(null)}
                  >
                    {/* Toggle or spinner */}
                    {isTransitioning ? (
                      <Loader2
                        className="size-[18px] animate-spin shrink-0"
                        style={{ color: stateColor }}
                      />
                    ) : (
                      <Toggle
                        on={isOn}
                        loading={isLoading}
                        onClick={() => handleToggle(entry)}
                      />
                    )}

                    {/* Name + Details */}
                    <div className="flex-1 min-w-0">
                      <div
                        className="font-semibold truncate text-[0.78rem]"
                      >
                        {entry.name}
                      </div>
                      <div
                        className="text-muted-foreground truncate mt-0.5 text-[0.68rem]"
                      >
                        {exchange}
                        {symbolDisplay !== "—" && (
                          <>
                            {" · "}
                            <span>{symbolDisplay}</span>
                          </>
                        )}
                        {entry.interval && (
                          <>
                            {" · "}
                            <span>{entry.interval}</span>
                          </>
                        )}
                        {" · "}
                        <span>{posCount} 个持仓</span>
                      </div>
                    </div>

                    {/* PnL */}
                    {pnl !== null && (
                      <span
                        className="font-semibold shrink-0"
                        style={{
                          fontSize: "0.78rem",
                          color: pnl >= 0 ? "var(--suc)" : "var(--dan)",
                        }}
                      >
                        {fmtPnl(pnl)}
                      </span>
                    )}

                    {/* Stop button (hover, running/paused only) */}
                    {canStop && isHovered && !isLoading && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setConfirmFlatten(entry.name);
                        }}
                        className="shrink-0 px-2.5 py-1 rounded text-[0.62rem] font-bold transition-all"
                        style={{
                          color: "var(--dan)",
                          background:
                            "color-mix(in srgb, var(--dan) 10%, transparent)",
                          border:
                            "1px solid color-mix(in srgb, var(--dan) 20%, transparent)",
                          transitionDuration: "var(--dur)",
                        }}
                      >
                        停止
                      </button>
                    )}

                    {/* State badge */}
                    <span
                      className="shrink-0 px-2 py-0.5 rounded font-bold"
                      style={{
                        fontSize: "0.56rem",
                        color: stateColor,
                        background: `color-mix(in srgb, ${stateColor} 12%, transparent)`,
                      }}
                    >
                      {stateLabel}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </FadeIn>

      {/* Confirm flatten-stop */}
      <ConfirmModal
        open={confirmFlatten !== null}
        onClose={() => setConfirmFlatten(null)}
        onConfirm={async () => {
          if (confirmFlatten) {
            await handleAction(confirmFlatten, "flatten-stop");
          }
          setConfirmFlatten(null);
        }}
        level="warning"
        title="平仓并停止"
        description={`确定要平仓并停止 "${confirmFlatten}" 吗？这将关闭所有持仓并移除策略。`}
        confirmLabel="确认平仓停止"
        loading={actionLoading !== null}
      />
    </div>
  );
}
