"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Plus, RefreshCw, Check, ChevronDown } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { apiGet, apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import { useWsEvent } from "@/providers/WebSocketProvider";
import type { RunStatus, TradeLogEntry, BacktestResult } from "./types";
import { OverviewTab } from "./components/OverviewTab";
import { TearsheetTab } from "./components/TearsheetTab";
import { TradeLogTab } from "./components/TradeLogTab";
import { ReportsTab } from "./components/ReportsTab";
import { PerformanceTab } from "./components/PerformanceTab";
import { TradesTab } from "./components/TradesTab";
import { RobustnessTab } from "./components/RobustnessTab";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface BacktestRunSummary {
  run_id: string;
  strategy_name: string;
  symbol: string;
  interval: string;
  start_date: string;
  end_date: string;
  status: RunStatus;
  created_at: string;
  progress_pct?: number | null;
  error?: string | null;
  result_summary?: {
    total_pnl?: number;
    total_return_pct?: number;
    sharpe_ratio?: number;
    win_rate?: number;
    profit_factor?: number;
    max_drawdown?: number;
    calmar_ratio?: number;
    total_trades?: number;
    sortino_ratio?: number;
  } | null;
}

interface StrategyInfo {
  name: string;
  type?: string;
}

interface ParamInfo {
  name: string;
  type: string;
  default: string | number | boolean | null;
}

interface CreateForm {
  strategy_name: string;
  start_date: string;
  end_date: string;
  initial_capital: string;
  maker_fee: string;
  taker_fee: string;
  latency_mode: string;
  latency_ms: string;
  fill_model_type: string;
  prob_fill_on_limit: string;
  prob_slippage: string;
  warmup_bars: string;
  tags: string;
}

const FILL_MODEL_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "default", label: "默认 (概率滑点)", hint: "可配置限价单成交概率和滑点概率" },
  { value: "best_price", label: "最优价成交", hint: "始终以最优买卖价成交，无滑点" },
  { value: "one_tick_slippage", label: "固定 1-tick 滑点", hint: "每笔成交固定滑动 1 个最小价格单位" },
  { value: "two_tier", label: "双层深度", hint: "10 手 @ 最优价，其余 @ ±1 tick" },
  { value: "three_tier", label: "三层深度 (50/30/20)", hint: "50 手 @ 最优 / 30 @ ±1 tick / 20 @ ±2 tick" },
  { value: "probabilistic", label: "概率模型 (50/50)", hint: "50% 最优价成交，50% 滑动 ±1 tick" },
  { value: "size_aware", label: "订单量感知", hint: "≤10 手最优价成交，>10 手产生市场冲击" },
  { value: "volume_sensitive", label: "成交量敏感", hint: "可用流动性 = 近期成交量 × 25%，超出部分 ±1 tick" },
  { value: "competition_aware", label: "竞争感知", hint: "模拟多参与者竞争，默认 30% 可见流动性" },
];

interface Subscription {
  exchange: string;
  symbol: string;
  granularity: "bar" | "tick";
  dataType?: string;
  timeframe?: string;
  timeframeValue?: number;
  timeframeUnit?: string;
  tickType?: string;
  depth?: number;
  snapMs?: number;
  auto: boolean;
}

/* ------------------------------------------------------------------ */
/*  Progress ring placeholder for running/queued backtests             */
/* ------------------------------------------------------------------ */

function RunningPlaceholder({ status, pct, message, fallbackMsg }: { status?: string; pct: number; message?: string; fallbackMsg?: string }) {
  const isRunning = status === "running" || status === "queued";
  if (!isRunning) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-muted-foreground">
          {status === "failed" ? "回测失败" : (fallbackMsg ?? "回测完成后可查看")}
        </span>
      </div>
    );
  }
  const isQueued = status === "queued";
  const radius = 80;
  const stroke = 6;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className="flex items-center justify-center h-full">
      <div className="flex flex-col items-center gap-6 w-80">
        <div className="relative">
          <svg width="184" height="184" viewBox="0 0 184 184">
            <circle cx="92" cy="92" r={radius} fill="none" stroke="var(--bg-t)" strokeWidth={stroke} />
            {!isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--acc)" strokeWidth={stroke}
                strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={offset}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            {isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--info)" strokeWidth={stroke}
                strokeLinecap="round" opacity="0.6"
                strokeDasharray={`${circumference * 0.25} ${circumference * 0.75}`}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", animation: "spin 1.5s linear infinite" }}
              />
            )}
            {!isQueued && pct > 0 && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--acc)" strokeWidth={stroke + 4}
                strokeLinecap="round" opacity="0.35"
                strokeDasharray={circumference} strokeDashoffset={offset}
                filter="url(#arcGlow)"
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            <defs>
              <filter id="arcGlow" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur in="SourceGraphic" stdDeviation="6" />
              </filter>
            </defs>
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            {isQueued ? (
              <span className="text-sm font-medium text-muted-foreground">排队中</span>
            ) : (
              <>
                <span className="text-4xl font-bold font-mono text-foreground" key={pct}>{pct}</span>
                <span className="text-sm font-medium text-muted-foreground -mt-0.5">%</span>
              </>
            )}
          </div>
        </div>
        <span className="text-sm font-medium text-muted-foreground">
          {isQueued ? "等待运行..." : (message || "回测运行中")}
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Copyable ID                                                        */
/* ------------------------------------------------------------------ */

function CopyableId({ runId }: { runId: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(runId);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <span className="bt-id" title={`点击复制: ${runId}`} onClick={handleCopy}>
      {copied ? (
        <><Check className="w-2.5 h-2.5 text-qds-success" /> copied</>
      ) : (
        runId.slice(0, 8)
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Run Row (for list view)                                            */
/* ------------------------------------------------------------------ */

interface ProgressDetail {
  elapsed_secs?: number;
  eta_secs?: number;
  total_bars?: number;
  processed_bars?: number;
  bars_per_sec?: number;
  trades?: number;
}

interface RunRowProps {
  run: BacktestRunSummary;
  progress: number | null;
  progressDetail?: ProgressDetail;
  expandedId: string | null;
  onToggleExpand: (id: string) => void;
  onViewDetail: (id: string) => void;
}

function fmtSecs(secs: number | undefined): string {
  if (secs == null || secs <= 0) return "—";
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function fmtBars(n: number | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function RunRow({ run, progress, progressDetail, expandedId, onToggleExpand, onViewDetail }: RunRowProps) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isRunning = run.status === "running";
  const isQueued = run.status === "queued";
  const isDone = run.status === "completed";
  const isFailed = run.status === "failed";
  const isExpandable = isRunning || isQueued;
  const isExpanded = expandedId === run.run_id;
  const pct = progress ?? 0;

  const statusKey = run.status === "completed" ? "done" : run.status === "running" ? "run" : run.status === "failed" ? "fail" : "queue";

  const statusLabel: Record<string, string> = {
    running: "Running",
    completed: "Done",
    failed: "Failed",
    queued: "Queued",
    cancelled: "Cancelled",
    cancelling: "Cancelling",
  };

  const handleRowClick = () => {
    if (isExpandable) {
      onToggleExpand(run.run_id);
    } else {
      onViewDetail(run.run_id);
    }
  };

  return (
    <div className={`bt-row ${isExpanded ? "expanded" : ""}`}>
      <div className="bt-row-main" onClick={handleRowClick}>
        <div className={`bt-accent bt-accent-${statusKey}`} />
        <div className="bt-row-info">
          <div className="bt-row-name">
            {run.strategy_name}
            {(() => {
              const syms = run.symbol.split(",").map((s) => s.trim()).filter(Boolean);
              if (syms.length <= 2) {
                return <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>{syms.join(", ")}</span>;
              }
              return (
                <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>
                  {syms.slice(0, 2).join(", ")}{" "}
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger className="text-muted-foreground" style={{ fontSize: ".65rem", cursor: "default" }}>
                        +{syms.length - 2}
                      </TooltipTrigger>
                      <TooltipContent side="right" className="max-w-xs">
                        <div className="flex flex-wrap gap-1">
                          {syms.map((s) => (
                            <span key={s} className="text-[10px]">{s}</span>
                          ))}
                        </div>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </span>
              );
            })()}
          </div>
          <div className="bt-row-meta">
            <CopyableId runId={run.run_id} />
            {run.interval} · {dateRange}
          </div>
        </div>

        <div className="bt-row-status">
          <span className={`bt-status bt-status-${statusKey}`}>
            {run.status === "running" && <span className="bt-pulse" />}
            {run.status === "completed" && "✓ "}
            {run.status === "failed" && "✕ "}
            {run.status === "queued" && "◦ "}
            {statusLabel[run.status] ?? run.status}
          </span>
        </div>

        <div className="bt-row-right">
          {isRunning && (
            <span className="text-primary">{pct}%</span>
          )}
          {isQueued && (
            <span className="flex items-center gap-[3px] text-qds-t3" style={{ fontFamily: "var(--font-d)", fontSize: ".65rem" }}>
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0s" }} />
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0.2s" }} />
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0.4s" }} />
            </span>
          )}
          {isDone && run.result_summary?.total_pnl != null ? (
            <span className={run.result_summary.total_pnl >= 0 ? "text-qds-success" : "text-destructive"}>
              {run.result_summary.total_pnl >= 0 ? "+" : ""}${Math.abs(run.result_summary.total_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          ) : isDone ? (
            <span className="text-qds-success">Completed</span>
          ) : null}
          {isFailed && <span className="text-destructive" style={{ fontSize: ".68rem" }}>{run.error ? run.error.slice(0, 16) : "Error"}</span>}
        </div>

        <div className="bt-row-action">
          {isExpandable && (
            <span
              className="text-[.68rem] text-[var(--t2)] cursor-pointer"
              style={{
                padding: ".75rem .65rem .75rem 0",
                transition: "transform .3s var(--eo)",
                display: "inline-block",
                transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
              }}
            >
              <ChevronDown className="w-3.5 h-3.5" />
            </span>
          )}
          {(isDone || isFailed) && (
            <button className="bt-view-btn" onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}>
              View <span style={{ transition: "transform 150ms" }}>&rarr;</span>
            </button>
          )}
        </div>
      </div>

      {isRunning && !isExpanded && (
        <div className="bt-progress">
          <div className="bt-progress-fill" style={{ width: `${pct}%` }} />
        </div>
      )}

      {isExpandable && (
        <div className={`bt-expand ${isExpanded ? "open" : ""}`}>
          <div className="bt-expand-inner">
            {isRunning && (
              <>
                <div style={{ height: 6, background: "var(--bg-t)", borderRadius: 3, overflow: "hidden", marginBottom: ".75rem", position: "relative" }}>
                  <div style={{ height: "100%", borderRadius: 3, background: "var(--acc)", transition: "width 1.5s var(--eo)", width: `${pct}%` }} />
                </div>
                <div className="bt-expand-stats">
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Progress</div>
                    <div className="bt-expand-stat-value text-primary">{pct}%</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Elapsed</div>
                    <div className="bt-expand-stat-value">{fmtSecs(progressDetail?.elapsed_secs)}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">ETA</div>
                    <div className="bt-expand-stat-value">{progressDetail?.eta_secs != null ? `~${fmtSecs(progressDetail.eta_secs)}` : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Speed</div>
                    <div className="bt-expand-stat-value">{progressDetail?.bars_per_sec != null ? `${fmtBars(progressDetail.bars_per_sec)}/s` : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Processed</div>
                    <div className="bt-expand-stat-value">
                      {progressDetail?.processed_bars != null
                        ? `${fmtBars(progressDetail.processed_bars)}${progressDetail.total_bars != null ? ` / ${fmtBars(progressDetail.total_bars)}` : ""}`
                        : "—"}
                    </div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Trades</div>
                    <div className="bt-expand-stat-value">{progressDetail?.trades != null ? progressDetail.trades : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Memory</div>
                    <div className="bt-expand-stat-value text-muted-foreground">—</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">CPU</div>
                    <div className="bt-expand-stat-value text-muted-foreground">—</div>
                  </div>
                </div>
              </>
            )}
            {isQueued && (
              <div>
                <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: "1rem" }}>
                  <div>
                    <div style={{ fontSize: ".6rem", color: "var(--t2)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: ".4rem" }}>Preview</div>
                    <div className="rounded-lg bg-secondary" style={{ height: 72 }} />
                  </div>
                  <div>
                    <div style={{ fontSize: ".6rem", color: "var(--t2)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: ".4rem" }}>Config</div>
                    <div className="flex flex-col gap-1.5">
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary" style={{ width: 55, height: 11 }} />
                        <div className="rounded bg-secondary flex-1" style={{ height: 11 }} />
                      </div>
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary" style={{ width: 65, height: 11 }} />
                        <div className="rounded bg-secondary flex-1" style={{ height: 11 }} />
                      </div>
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary" style={{ width: 45, height: 11 }} />
                        <div className="rounded bg-secondary flex-1" style={{ height: 11 }} />
                      </div>
                    </div>
                  </div>
                </div>
                <div style={{ marginTop: ".75rem", fontFamily: "var(--font-d)", fontSize: ".7rem", color: "var(--t2)" }}>
                  Estimated start in <span style={{ color: "var(--acc)" }}>~12 min</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  History Row (simplified for completed/failed)                      */
/* ------------------------------------------------------------------ */

function HistoryRow({ run, expanded, onToggleExpand, onViewDetail }: {
  run: BacktestRunSummary;
  expanded: boolean;
  onToggleExpand: (id: string) => void;
  onViewDetail: (id: string) => void;
}) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isDone = run.status === "completed";
  const statusKey = isDone ? "done" : "fail";
  const s = run.result_summary;

  return (
    <div className={`bt-row ${expanded ? "expanded" : ""}`}>
      <div className="bt-row-main" onClick={() => onToggleExpand(run.run_id)}>
        <div className={`bt-accent bt-accent-${statusKey}`} />
        <div className="bt-row-info">
          <div className="bt-row-name">
            {run.strategy_name}
            {(() => {
              const syms = run.symbol.split(",").map((s) => s.trim()).filter(Boolean);
              if (syms.length <= 2) {
                return <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>{syms.join(", ")}</span>;
              }
              return (
                <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>
                  {syms.slice(0, 2).join(", ")} +{syms.length - 2}
                </span>
              );
            })()}
          </div>
          <div className="bt-row-meta">
            <CopyableId runId={run.run_id} />
            {run.interval} · {dateRange}
          </div>
        </div>

        <div className="bt-row-status">
          <span className={`bt-status bt-status-${statusKey}`}>
            {isDone ? "✓ Done" : "✕ Failed"}
          </span>
        </div>

        <div className="bt-row-right">
          {isDone && s?.total_pnl != null ? (
            <span className={s.total_pnl >= 0 ? "text-qds-success" : "text-destructive"}>
              {s.total_pnl >= 0 ? "+" : "-"}${Math.abs(s.total_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          ) : isDone ? (
            <span className="text-qds-success">Completed</span>
          ) : (
            <span className="text-destructive" style={{ fontSize: ".68rem" }}>{run.error ? run.error.slice(0, 24) : "Error"}</span>
          )}
        </div>

        <div className="bt-row-action">
          <span
            className="text-[.68rem] text-[var(--t3)] cursor-pointer"
            style={{ padding: ".75rem .65rem .75rem .25rem", transition: "color 150ms", lineHeight: 1 }}
          >▾</span>
        </div>
      </div>

      <div className={`bt-expand ${expanded ? "open" : ""}`}>
        <div className="bt-expand-inner">
          {isDone && s ? (
            <>
              <div className="bt-expand-stats" style={{ gridTemplateColumns: "repeat(4, 1fr)", marginBottom: ".75rem" }}>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">Sharpe</div>
                  <div className={`bt-expand-stat-value ${(s.sharpe_ratio ?? 0) >= 1 ? "text-qds-success" : (s.sharpe_ratio ?? 0) >= 0 ? "" : "text-destructive"}`}>
                    {s.sharpe_ratio?.toFixed(2) ?? "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">Win Rate</div>
                  <div className="bt-expand-stat-value">{s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : "—"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">Profit Factor</div>
                  <div className={`bt-expand-stat-value ${(s.profit_factor ?? 0) >= 1.5 ? "text-qds-success" : ""}`}>
                    {s.profit_factor?.toFixed(2) ?? "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">Max DD</div>
                  <div className="bt-expand-stat-value text-destructive">{s.max_drawdown != null ? `${(s.max_drawdown * 100).toFixed(1)}%` : "—"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">Calmar</div>
                  <div className={`bt-expand-stat-value ${(s.calmar_ratio ?? 0) >= 1 ? "text-qds-success" : (s.calmar_ratio ?? 0) >= 0 ? "" : "text-destructive"}`}>
                    {s.calmar_ratio?.toFixed(2) ?? "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">Trades</div>
                  <div className="bt-expand-stat-value">{s.total_trades?.toLocaleString() ?? "—"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">PnL</div>
                  <div className={`bt-expand-stat-value ${(s.total_pnl ?? 0) >= 0 ? "text-qds-success" : "text-destructive"}`}>
                    {s.total_pnl != null ? `${s.total_pnl >= 0 ? "+" : "-"}$${Math.abs(s.total_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">Return</div>
                  <div className={`bt-expand-stat-value ${(s.total_return_pct ?? 0) >= 0 ? "text-qds-success" : "text-destructive"}`}>
                    {s.total_return_pct != null ? `${s.total_return_pct >= 0 ? "+" : ""}${s.total_return_pct.toFixed(1)}%` : "—"}
                  </div>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button className="bt-view-btn" onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}>
                  查看完整报告 <span>&rarr;</span>
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="bt-expand-stats" style={{ gridTemplateColumns: "repeat(3, 1fr)", marginBottom: ".75rem" }}>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">错误类型</div>
                  <div className="bt-expand-stat-value text-destructive">{run.error ?? "Unknown error"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">品种</div>
                  <div className="bt-expand-stat-value">{run.symbol}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="bt-expand-stat-label">策略</div>
                  <div className="bt-expand-stat-value">{run.strategy_name}</div>
                </div>
              </div>
              <div style={{ display: "flex", gap: ".4rem", justifyContent: "flex-end" }}>
                <button className="bt-view-btn" onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}>
                  查看日志 <span>&rarr;</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Pagination                                                         */
/* ------------------------------------------------------------------ */

function Pager({ curPage, totalPages, total, pageSize, onPageChange }: {
  curPage: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPageChange: (p: number) => void;
}) {
  const start = (curPage - 1) * pageSize + 1;
  const end = Math.min(curPage * pageSize, total);

  const maxBtns = 7;
  let pStart = Math.max(1, curPage - 3);
  let pEnd = Math.min(totalPages, pStart + maxBtns - 1);
  if (pEnd - pStart < maxBtns - 1) pStart = Math.max(1, pEnd - maxBtns + 1);

  const buttons: React.ReactNode[] = [];
  if (pStart > 1) {
    buttons.push(<button key="p1" className="bt-pager-btn" onClick={() => onPageChange(1)}>1</button>);
    if (pStart > 2) buttons.push(<span key="d1" className="bt-pager-dots">...</span>);
  }
  for (let i = pStart; i <= pEnd; i++) {
    buttons.push(
      <button key={i} className={`bt-pager-btn ${i === curPage ? "active" : ""}`} onClick={() => onPageChange(i)}>{i}</button>
    );
  }
  if (pEnd < totalPages) {
    if (pEnd < totalPages - 1) buttons.push(<span key="d2" className="bt-pager-dots">...</span>);
    buttons.push(<button key={`p${totalPages}`} className="bt-pager-btn" onClick={() => onPageChange(totalPages)}>{totalPages}</button>);
  }

  return (
    <div className="bt-pager">
      <span>{start}&ndash;{end} / {total}</span>
      <div className="bt-pager-nav">
        <button className="bt-pager-btn" disabled={curPage <= 1} onClick={() => onPageChange(1)}>&laquo;</button>
        <button className="bt-pager-btn" disabled={curPage <= 1} onClick={() => onPageChange(curPage - 1)}>&lsaquo;</button>
        {buttons}
        <button className="bt-pager-btn" disabled={curPage >= totalPages} onClick={() => onPageChange(curPage + 1)}>&rsaquo;</button>
        <button className="bt-pager-btn" disabled={curPage >= totalPages} onClick={() => onPageChange(totalPages)}>&raquo;</button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Create Form                                                        */
/* ------------------------------------------------------------------ */

function parseTimeframe(tf: string): { value: number; unit: string; clean: string } {
  const m = tf.match(/^(\d+)(s|m|h|d|min|hour)$/i);
  if (!m) return { value: 5, unit: "m", clean: "5m" };
  let unit = m[2].toLowerCase();
  if (unit === "min") unit = "m";
  if (unit === "hour") unit = "h";
  const value = parseInt(m[1]) || 1;
  return { value, unit, clean: `${value}${unit}` };
}

function CreateFormView({ strategies, onSubmit, onCancel }: {
  strategies: StrategyInfo[];
  onSubmit: () => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<CreateForm>({
    strategy_name: "",
    start_date: "2026-01-01",
    end_date: "2026-03-31",
    initial_capital: "100,000",
    maker_fee: "0.02%",
    taker_fee: "0.05%",
    latency_mode: "fixed",
    latency_ms: "5",
    fill_model_type: "one_tick_slippage",
    prob_fill_on_limit: "1.0",
    prob_slippage: "0.0",
    warmup_bars: "200",
    tags: "",
  });
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<{ total_bars: number; estimated_label: string } | null>(null);
  const [strategyParams, setStrategyParams] = useState<ParamInfo[]>([]);
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const [paramOverrides, setParamOverrides] = useState<Record<string, string>>({});
  const [strategySearch, setStrategySearch] = useState("");
  const [strategyDropdownOpen, setStrategyDropdownOpen] = useState(false);
  const [allSymbols, setAllSymbols] = useState<{symbol: string; base: string; quote: string}[]>([]);
  const [symbolDropdownIdx, setSymbolDropdownIdx] = useState<number | null>(null);
  const [symbolSearchText, setSymbolSearchText] = useState("");
  const strategyRef = useRef<HTMLDivElement>(null);
  const sectionsRef = useRef<HTMLDivElement>(null);

  // Fetch Binance symbols on mount
  useEffect(() => {
    apiGet<{symbol: string; base: string; quote: string}[]>("/api/data/symbols")
      .then(res => { if (res) setAllSymbols(res); })
      .catch(() => {});
  }, []);

  // Animate form sections on mount
  useEffect(() => {
    const timer = setTimeout(() => {
      sectionsRef.current?.querySelectorAll(".bt-form-section").forEach((el) => el.classList.add("v"));
    }, 50);
    return () => clearTimeout(timer);
  }, []);

  // Close strategy dropdown on outside click
  useEffect(() => {
    if (!strategyDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (strategyRef.current && !strategyRef.current.contains(e.target as Node)) {
        setStrategyDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [strategyDropdownOpen]);

  // Fetch estimate when subscriptions or date range change
  useEffect(() => {
    const symbols = [...new Set(subscriptions.map(s => s.symbol))];
    const barSubs = subscriptions.filter(s => s.granularity === "bar");
    const interval = barSubs.length > 0 ? (barSubs[0].timeframe || "5m") : "5m";
    if (symbols.length === 0 || !form.start_date || !form.end_date) { setEstimate(null); return; }
    const timer = setTimeout(() => {
      apiPost<{ total_bars: number; estimated_label: string }>("/api/backtest/estimate", {
        symbols,
        interval,
        start_date: form.start_date,
        end_date: form.end_date,
      }).then((d) => d && setEstimate(d)).catch(() => setEstimate(null));
    }, 300);
    return () => clearTimeout(timer);
  }, [subscriptions, form.start_date, form.end_date]);

  // Fetch strategy params when strategy changes
  useEffect(() => {
    if (!form.strategy_name) {
      setStrategyParams([]);
      return;
    }
    apiGet<{ name: string; config_params: ParamInfo[]; optimize_ranges?: unknown }>(`/api/strategies/${encodeURIComponent(form.strategy_name)}/params`)
      .then((d) => {
        if (d?.config_params) {
          setStrategyParams(d.config_params);
          setParamOverrides({});
          setParamsExpanded(false);
        }
      })
      .catch(() => setStrategyParams([]));
  }, [form.strategy_name]);

  // Fetch strategy defaults and populate subscriptions when strategy changes
  useEffect(() => {
    if (!form.strategy_name) { setSubscriptions([]); return; }
    apiGet<{ symbols: string[]; interval: string | null; subscriptions?: Array<{
      exchange: string; symbol: string; granularity: string; timeframe: string | null; tick_type: string | null; auto: boolean;
    }> }>(`/api/strategies/${encodeURIComponent(form.strategy_name)}/defaults`)
      .then((d) => {
        if (d?.subscriptions?.length) {
          setSubscriptions(d.subscriptions.map(s => {
            const parsed = parseTimeframe(s.timeframe || "5m");
            return {
              exchange: "binance",
              symbol: s.symbol,
              granularity: (s.granularity as "bar" | "tick") || "bar",
              dataType: s.granularity === "tick" ? "aggTrades" : "klines",
              timeframe: parsed.clean,
              timeframeValue: parsed.value,
              timeframeUnit: parsed.unit,
              tickType: s.tick_type || "trades",
              auto: s.auto,
            };
          }));
        } else if (d?.symbols?.length) {
          const parsed = parseTimeframe(d.interval || "5m");
          setSubscriptions(d.symbols.map(sym => ({
            exchange: "binance", symbol: sym, granularity: "bar" as const,
            dataType: "klines", timeframe: parsed.clean, timeframeValue: parsed.value, timeframeUnit: parsed.unit, auto: true,
          })));
        }
      })
      .catch(() => {});
  }, [form.strategy_name]);

  const filteredStrategies = strategies.filter((s) =>
    s.name.toLowerCase().includes(strategySearch.toLowerCase())
  );

  const submitAction = useAction(
    async () => {
      const capitalNum = parseFloat(form.initial_capital.replace(/,/g, "")) || 100000;
      const params: Record<string, string> = {};
      for (const [k, v] of Object.entries(paramOverrides)) {
        if (v.trim()) params[k] = v.trim();
      }
      const symbols = [...new Set(subscriptions.map(s => s.symbol))];
      const barSubs = subscriptions.filter(s => s.granularity === "bar");
      const interval = barSubs.length > 0 ? (barSubs[0].timeframe || "5m") : "5m";
      // Construct fill_model — always include latency_ms (0 when off)
      const fill_model: Record<string, unknown> = {};
      if (form.latency_mode !== "off") {
        fill_model.latency_mode = form.latency_mode;
        fill_model.latency_ms = parseFloat(form.latency_ms) || 5;
      } else {
        fill_model.latency_ms = 0;
      }
      // FillModel type selection (maps to NT built-in models)
      fill_model.fill_model_type = form.fill_model_type;
      if (form.fill_model_type === "default") {
        fill_model.prob_fill_on_limit = parseFloat(form.prob_fill_on_limit) || 1.0;
        fill_model.prob_slippage = parseFloat(form.prob_slippage) || 0.0;
      }
      return apiPost("/api/backtest/run", {
        strategy: form.strategy_name,
        symbols,
        interval,
        start_date: form.start_date,
        end_date: form.end_date,
        initial_capital: capitalNum,
        params: Object.keys(params).length > 0 ? params : undefined,
        data_type: barSubs[0]?.dataType || "klines",
        maker_fee: form.maker_fee || undefined,
        taker_fee: form.taker_fee || undefined,
        fill_model,
        warmup_bars: form.warmup_bars ? parseInt(form.warmup_bars) : undefined,
        tags: form.tags || undefined,
      });
    },
    {
      successDuration: 1200,
      onSuccess: async () => {
        await onSubmit();
        onCancel();
      },
    }
  );

  const handleSubmit = async () => {
    setSubmitError(null);
    if (!form.strategy_name) { setSubmitError("请选择策略"); return; }
    if (subscriptions.length === 0) { setSubmitError("请添加数据订阅"); return; }
    if (!form.start_date || !form.end_date) { setSubmitError("请填写日期范围"); return; }
    await submitAction.execute();
  };

  const typeColors: Record<string, string> = { float: "var(--info)", int: "var(--suc)", bool: "var(--warn)" };

  return (
    <div ref={sectionsRef}>
      {/* Back + Title */}
      <div className="bt-form-section" style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1rem", paddingBottom: "1rem", borderBottom: "1px solid var(--bd)" }}>
        <button className="bt-view-btn" onClick={onCancel} style={{ fontSize: ".72rem", padding: ".35rem .7rem" }}>
          <span style={{ transition: "transform 150ms" }}>&larr;</span> 返回
        </button>
        <div>
          <div style={{ fontFamily: "var(--font-d)", fontSize: "1rem", fontWeight: 600 }}>创建回测</div>
          <div style={{ fontSize: ".72rem", color: "var(--t2)" }}>配置策略、数据范围和参数，提交后加入队列</div>
        </div>
      </div>

      {/* Section 1: Strategy */}
      <div className="bt-form-section" style={strategyDropdownOpen ? { zIndex: 10, position: "relative" } : undefined}>
        <div className="qds-section-label">策略</div>
        <div className="bt-form-row">
          <div className="bt-form-group" ref={strategyRef}>
            <div className="bt-form-label">策略 <span className="req">*</span></div>
            <div className="relative">
              <input
                value={strategyDropdownOpen ? strategySearch : form.strategy_name || ""}
                onChange={(e) => { setStrategySearch(e.target.value); setStrategyDropdownOpen(true); }}
                onFocus={() => { setStrategySearch(""); setStrategyDropdownOpen(true); }}
                placeholder="搜索策略..."
                className="qds-input"
              />
              {strategyDropdownOpen && (
                <div className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-lg border bg-input shadow-xl animate-in fade-in slide-in-from-top-1 duration-150">
                  {filteredStrategies.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-muted-foreground">无匹配策略</div>
                  ) : (
                    filteredStrategies.map((s) => (
                      <button
                        key={s.name}
                        type="button"
                        onClick={() => {
                          setForm((f) => ({ ...f, strategy_name: s.name }));
                          setStrategyDropdownOpen(false);
                          setStrategySearch("");
                        }}
                        className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-secondary transition-colors ${
                          form.strategy_name === s.name ? "text-primary" : "text-foreground"
                        }`}
                      >
                        <span className="font-medium">{s.name}</span>
                        {s.type === "portfolio" && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-qds-accent-dim text-qds-info font-medium">组合</span>
                        )}
                        {form.strategy_name === s.name && <Check className="w-3.5 h-3.5 text-primary" />}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
            <div style={{ fontSize: ".65rem", color: "var(--t3)", marginTop: ".1rem" }}>选择策略后自动填充数据订阅</div>
          </div>
          <div />
        </div>
      </div>

      {/* Section 2: Data Subscriptions */}
      <div className="bt-form-section">
        <div className="qds-section-label">
          数据订阅
          <span style={{ fontWeight: 400, color: "var(--t2)", letterSpacing: 0, textTransform: "none", fontSize: ".55rem" }}>
            {subscriptions.length > 0 ? `· ${subscriptions.length} 个数据源` : "· 选择策略后自动填充"}
          </span>
        </div>
        <div style={{ background: "var(--bg-p)", border: "1px solid var(--bd)", borderRadius: "var(--r)", overflow: "hidden" }}>
          {subscriptions.length === 0 ? (
            <div style={{ padding: "2.5rem 2rem", textAlign: "center" }}>
              <div style={{ fontSize: "1.2rem", color: "var(--t3)", marginBottom: ".6rem" }}>⧖</div>
              <div style={{ fontSize: ".78rem", color: "var(--t2)", marginBottom: ".2rem" }}>选择策略后自动填充数据订阅</div>
              <div style={{ fontSize: ".68rem", color: "var(--t3)" }}>策略定义需要订阅的交易所、品种和数据粒度</div>
            </div>
          ) : (
            <>
              <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--font-d)", fontSize: ".8rem" }}>
                <thead>
                  <tr>
                    <th style={{ fontSize: ".7rem", fontWeight: 500, color: "var(--t2)", padding: ".7rem 1.25rem", letterSpacing: ".08em", borderBottom: "1px solid var(--bd)", textAlign: "left", textTransform: "uppercase" }}>品种</th>
                    <th style={{ fontSize: ".7rem", fontWeight: 500, color: "var(--t2)", padding: ".7rem 1.25rem", letterSpacing: ".08em", borderBottom: "1px solid var(--bd)", textAlign: "left", textTransform: "uppercase" }}>数据类型</th>
                    <th style={{ fontSize: ".7rem", fontWeight: 500, color: "var(--t2)", padding: ".7rem 1.25rem", letterSpacing: ".08em", borderBottom: "1px solid var(--bd)", textAlign: "left", textTransform: "uppercase" }}>周期</th>
                    <th style={{ fontSize: ".7rem", fontWeight: 500, color: "var(--t2)", padding: ".7rem 1.25rem", letterSpacing: ".08em", borderBottom: "1px solid var(--bd)", width: "32px" }} />
                  </tr>
                </thead>
                <tbody>
                  {subscriptions.map((sub, idx) => {
                    const ctrlStyle: React.CSSProperties = {
                      fontFamily: "var(--font-d)",
                      fontSize: ".82rem",
                      padding: ".5rem .75rem",
                      background: "var(--bg-in)",
                      border: "1px solid var(--bd)",
                      borderRadius: "var(--rs)",
                      color: "var(--t0)",
                      outline: "none",
                      transition: "border-color 150ms, box-shadow 300ms",
                    };
                    const updateSub = (patch: Partial<Subscription>) => {
                      setSubscriptions(prev => prev.map((s, i) => i === idx ? { ...s, ...patch, auto: false } : s));
                    };
                    return (
                      <tr key={idx} style={{ borderBottom: "1px solid var(--bd)", transition: "background 150ms" }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-t)")}
                        onMouseLeave={(e) => (e.currentTarget.style.background = "")}
                      >
                        {/* 品种 */}
                        <td style={{ padding: ".65rem 1.25rem", verticalAlign: "middle", position: "relative" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: ".5rem" }}>
                            <span style={{ fontSize: ".65rem", color: "var(--t3)", fontWeight: 400, flexShrink: 0 }}>Binance</span>
                            <input
                              style={{ ...ctrlStyle, cursor: "text", width: "150px" }}
                              value={symbolDropdownIdx === idx ? symbolSearchText : sub.symbol}
                              onChange={(e) => { setSymbolSearchText(e.target.value.toUpperCase()); setSymbolDropdownIdx(idx); }}
                              onFocus={(e) => { setSymbolSearchText(sub.symbol); setSymbolDropdownIdx(idx); e.currentTarget.style.borderColor = "var(--acc)"; e.currentTarget.style.boxShadow = "0 0 0 3px var(--acc-d)"; }}
                              onBlur={(e) => {
                                e.currentTarget.style.borderColor = "var(--bd)"; e.currentTarget.style.boxShadow = "none";
                                setTimeout(() => {
                                  if (symbolDropdownIdx === idx) {
                                    if (symbolSearchText.trim()) updateSub({ symbol: symbolSearchText.trim() });
                                    setSymbolDropdownIdx(null);
                                  }
                                }, 200);
                              }}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") {
                                  if (symbolSearchText.trim()) updateSub({ symbol: symbolSearchText.trim() });
                                  setSymbolDropdownIdx(null);
                                  (e.target as HTMLInputElement).blur();
                                }
                              }}
                              placeholder="搜索品种..."
                            />
                          </div>
                          {symbolDropdownIdx === idx && (
                            <div style={{
                              position: "absolute", top: "100%", left: "1.25rem", zIndex: 50,
                              maxHeight: "220px", overflowY: "auto", width: "240px",
                              background: "var(--bg-p)", border: "1px solid var(--bd)", borderRadius: "10px",
                              boxShadow: "0 12px 40px rgba(0,0,0,.15)", marginTop: "4px", padding: ".35rem",
                            }}>
                              {allSymbols
                                .filter(s => {
                                  const q = symbolSearchText.toUpperCase();
                                  return !q || s.symbol.includes(q) || s.base.includes(q);
                                })
                                .slice(0, 50)
                                .map(s => (
                                  <button key={s.symbol} type="button"
                                    style={{
                                      width: "100%", textAlign: "left", padding: ".4rem .65rem",
                                      fontFamily: "var(--font-d)", fontSize: ".78rem", border: "none", borderRadius: "var(--rs)",
                                      background: s.symbol === sub.symbol ? "var(--bg-t)" : "transparent",
                                      color: "var(--t0)", cursor: "pointer", display: "block",
                                      transition: "background 150ms, transform 150ms",
                                    }}
                                    onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-t)"; e.currentTarget.style.transform = "translateX(3px)"; }}
                                    onMouseLeave={(e) => { e.currentTarget.style.background = s.symbol === sub.symbol ? "var(--bg-t)" : "transparent"; e.currentTarget.style.transform = "none"; }}
                                    onMouseDown={(e) => {
                                      e.preventDefault();
                                      updateSub({ symbol: s.symbol });
                                      setSymbolDropdownIdx(null);
                                    }}
                                  >
                                    <span style={{ fontWeight: 500 }}>{s.symbol}</span>
                                    <span style={{ fontSize: ".65rem", color: "var(--t3)", marginLeft: ".5rem" }}>{s.base}</span>
                                  </button>
                                ))}
                              {allSymbols.filter(s => { const q = symbolSearchText.toUpperCase(); return !q || s.symbol.includes(q) || s.base.includes(q); }).length === 0 && (
                                <div style={{ padding: ".5rem .65rem", fontSize: ".72rem", color: "var(--t3)" }}>无匹配 — 按 Enter 使用自定义值</div>
                              )}
                            </div>
                          )}
                        </td>
                        {/* 数据类型 */}
                        <td style={{ padding: ".65rem 1.25rem", verticalAlign: "middle" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: ".4rem" }}>
                            <select style={{ ...ctrlStyle, cursor: "pointer", padding: ".5rem .6rem" }} value={sub.granularity} onChange={(e) => {
                              const g = e.target.value as "bar" | "tick";
                              updateSub({ granularity: g, dataType: g === "bar" ? "klines" : "aggTrades" });
                            }}>
                              <option value="bar">Bar</option>
                              <option value="tick">Tick</option>
                            </select>
                            <span style={{ color: "var(--t3)", fontSize: ".7rem" }}>·</span>
                            <select style={{ ...ctrlStyle, cursor: "pointer", padding: ".5rem .6rem" }}
                              value={sub.dataType || (sub.granularity === "bar" ? "klines" : "aggTrades")}
                              onChange={(e) => updateSub({ dataType: e.target.value })}
                            >
                              {sub.granularity === "bar" ? (
                                <>
                                  <option value="klines">klines</option>
                                  <option value="markPriceKlines">markPrice</option>
                                  <option value="indexPriceKlines">indexPrice</option>
                                  <option value="premiumIndexKlines">premiumIndex</option>
                                </>
                              ) : (
                                <>
                                  <option value="aggTrades">aggTrades</option>
                                  <option value="trades">trades</option>
                                </>
                              )}
                            </select>
                          </div>
                        </td>
                        {/* 周期 */}
                        <td style={{ padding: ".65rem 1.25rem", verticalAlign: "middle" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: ".5rem" }}>
                            {sub.auto && (
                              <span style={{
                                fontFamily: "var(--font-d)", fontSize: ".6rem", padding: ".15rem .4rem", borderRadius: "3px",
                                background: "var(--info-d)", color: "var(--info)", flexShrink: 0,
                              }}>auto</span>
                            )}
                            {sub.granularity === "bar" ? (
                              <span style={{ display: "inline-flex", alignItems: "center", background: "var(--bg-in)", border: "1px solid var(--bd)", borderRadius: "var(--rs)", overflow: "hidden", transition: "border-color 150ms, box-shadow 300ms" }}>
                                <input
                                  type="number"
                                  min={1}
                                  step={1}
                                  value={sub.timeframeValue ?? 5}
                                  onChange={(e) => {
                                    const v = Math.max(1, Math.round(Number(e.target.value) || 1));
                                    const unit = sub.timeframeUnit || "m";
                                    updateSub({ timeframeValue: v, timeframeUnit: unit, timeframe: `${v}${unit}` });
                                  }}
                                  onFocus={(e) => { const p = e.currentTarget.parentElement as HTMLElement; p.style.borderColor = "var(--acc)"; p.style.boxShadow = "0 0 0 3px var(--acc-d)"; }}
                                  onBlur={(e) => { const p = e.currentTarget.parentElement as HTMLElement; p.style.borderColor = "var(--bd)"; p.style.boxShadow = "none"; }}
                                  style={{ width: "40px", textAlign: "center", border: "none", background: "transparent", padding: ".45rem .15rem", fontFamily: "var(--font-d)", fontSize: ".82rem", fontWeight: 500, color: "var(--t0)", outline: "none", MozAppearance: "textfield" as never }}
                                />
                                <span style={{ width: "1px", alignSelf: "stretch", background: "var(--bd)", flexShrink: 0 }} />
                                <select
                                  style={{ border: "none", background: "transparent", padding: ".45rem .5rem .45rem .4rem", fontFamily: "var(--font-d)", fontSize: ".82rem", fontWeight: 500, color: "var(--t0)", outline: "none", cursor: "pointer" }}
                                  value={sub.timeframeUnit || "m"}
                                  onChange={(e) => {
                                    const unit = e.target.value;
                                    const v = sub.timeframeValue ?? 5;
                                    updateSub({ timeframeUnit: unit, timeframe: `${v}${unit}` });
                                  }}
                                >
                                  <option value="s">秒</option>
                                  <option value="m">分</option>
                                  <option value="h">时</option>
                                  <option value="d">天</option>
                                </select>
                              </span>
                            ) : (
                              <select style={{ ...ctrlStyle, cursor: "pointer", padding: ".5rem .6rem" }}
                                value={sub.tickType || "trades"}
                                onChange={(e) => updateSub({ tickType: e.target.value })}
                              >
                                <option value="trades">Trade ticks</option>
                                <option value="quotes">Quote BBO</option>
                                <option value="l2">L2 Orderbook</option>
                              </select>
                            )}
                          </div>
                        </td>
                        <td style={{ padding: ".65rem .75rem", verticalAlign: "middle" }}>
                          <button
                            onClick={() => setSubscriptions(prev => prev.filter((_, i) => i !== idx))}
                            style={{
                              width: "24px", height: "24px", borderRadius: "var(--rs)", border: "1px solid transparent",
                              background: "none", color: "var(--t3)", cursor: "pointer", display: "flex",
                              alignItems: "center", justifyContent: "center", fontSize: ".75rem", transition: "all 150ms",
                            }}
                            onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--dan)"; e.currentTarget.style.color = "var(--dan)"; e.currentTarget.style.background = "var(--dan-d)"; }}
                            onMouseLeave={(e) => { e.currentTarget.style.borderColor = "transparent"; e.currentTarget.style.color = "var(--t3)"; e.currentTarget.style.background = "none"; }}
                          >
                            ×
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div style={{ padding: ".45rem .65rem", borderTop: "1px solid var(--bd)", display: "flex", justifyContent: "flex-end" }}>
                <button
                  onClick={() => setSubscriptions(prev => [...prev, { exchange: "binance", symbol: "BTCUSDT-PERP", granularity: "bar", dataType: "klines", timeframe: "5m", timeframeValue: 5, timeframeUnit: "m", auto: false }])}
                  style={{
                    fontFamily: "var(--font-d)", fontSize: ".62rem", padding: ".25rem .55rem", borderRadius: "var(--rs)",
                    border: "1px solid var(--bd)", background: "none", color: "var(--t1)", cursor: "pointer", transition: "all 150ms",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.borderColor = "var(--bdh)"; e.currentTarget.style.color = "var(--t0)"; e.currentTarget.style.background = "var(--bg-t)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.borderColor = "var(--bd)"; e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.background = "none"; }}
                >
                  + 添加订阅
                </button>
              </div>
            </>
          )}
        </div>
        {subscriptions.some(s => s.granularity === "tick") && (
          <div style={{
            display: "flex", marginTop: ".65rem", padding: ".6rem .85rem",
            background: "var(--warn-d)", border: "1px solid color-mix(in srgb, var(--warn) 30%, transparent)",
            borderRadius: "var(--rs)", fontFamily: "var(--font-d)", fontSize: ".7rem", color: "var(--warn)",
            alignItems: "flex-start", gap: ".5rem",
          }}>
            <span style={{ fontSize: ".85rem", lineHeight: 1, flexShrink: 0 }}>⚠</span>
            <div>
              <div style={{ fontWeight: 600, marginBottom: ".15rem" }}>数据量提醒</div>
              <div style={{ fontWeight: 400, lineHeight: 1.5 }}>
                包含 {subscriptions.filter(s => s.granularity === "tick").length} 个 tick 数据源。Tick 回测数据量大、运行时间长。建议先用短时间范围验证策略逻辑。
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Section 2: Time Range & Capital */}
      <div className="bt-form-section">
        <div className="qds-section-label">时间范围 &amp; 资金</div>
        <div className="bt-form-row-3">
          <div className="bt-form-group">
            <div className="bt-form-label">开始日期 <span className="req">*</span></div>
            <input className="qds-input" type="date" value={form.start_date} onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">结束日期 <span className="req">*</span></div>
            <input className="qds-input" type="date" value={form.end_date} onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">初始资金 (USD) <span className="req">*</span></div>
            <input className="qds-input" type="text" value={form.initial_capital} onChange={(e) => setForm((f) => ({ ...f, initial_capital: e.target.value }))} placeholder="e.g. 100000" />
          </div>
        </div>
      </div>

      {/* Section 2b: Simulation Environment — 3 sub-cards */}
      <div className="bt-form-section">
        <div className="qds-section-label">模拟环境</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: ".75rem" }}>
          {/* Card 1: Fee Model */}
          <div style={{
            background: "var(--bg-p)", border: "1px solid var(--bd)", borderRadius: "var(--r)",
            padding: ".85rem", display: "flex", flexDirection: "column", gap: ".6rem",
            transition: "border-color 150ms",
          }}>
            <div style={{ fontFamily: "var(--font-d)", fontSize: ".6rem", color: "var(--t2)", textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600 }}>手续费</div>
            <div>
              <div className="bt-form-label">Maker</div>
              <input className="qds-input" type="text" value={form.maker_fee} onChange={(e) => setForm((f) => ({ ...f, maker_fee: e.target.value }))} placeholder="0.02%" />
            </div>
            <div>
              <div className="bt-form-label">Taker</div>
              <input className="qds-input" type="text" value={form.taker_fee} onChange={(e) => setForm((f) => ({ ...f, taker_fee: e.target.value }))} placeholder="0.05%" />
            </div>
            <div style={{ fontFamily: "var(--font-d)", fontSize: ".6rem", color: "var(--t3)", marginTop: "auto" }}>
              MakerTakerFeeModel · 百分比或小数
            </div>
          </div>

          {/* Card 2: Latency Model */}
          <div style={{
            background: "var(--bg-p)", border: "1px solid var(--bd)", borderRadius: "var(--r)",
            padding: ".85rem", display: "flex", flexDirection: "column", gap: ".6rem",
            transition: "border-color 150ms",
          }}>
            <div style={{ fontFamily: "var(--font-d)", fontSize: ".6rem", color: "var(--t2)", textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600 }}>延迟模拟</div>
            <div>
              <div className="bt-form-label">模式</div>
              <select className="qds-select" value={form.latency_mode} onChange={(e) => setForm((f) => ({ ...f, latency_mode: e.target.value }))}>
                <option value="off">关闭</option>
                <option value="fixed">固定延迟</option>
              </select>
            </div>
            <div style={form.latency_mode === "off" ? { opacity: 0.35, pointerEvents: "none" } : undefined}>
              <div className="bt-form-label">延迟 (ms)</div>
              <input
                className="qds-input"
                type="text"
                value={form.latency_ms}
                onChange={(e) => setForm((f) => ({ ...f, latency_ms: e.target.value }))}
                placeholder="5"
                disabled={form.latency_mode === "off"}
              />
            </div>
            <div style={{ fontFamily: "var(--font-d)", fontSize: ".6rem", color: "var(--t3)", marginTop: "auto" }}>
              LatencyModel · 订单到达交易所的网络延迟
            </div>
          </div>

          {/* Card 3: Fill Model */}
          <div style={{
            background: "var(--bg-p)", border: "1px solid var(--bd)", borderRadius: "var(--r)",
            padding: ".85rem", display: "flex", flexDirection: "column", gap: ".6rem",
            transition: "border-color 150ms",
          }}>
            <div style={{ fontFamily: "var(--font-d)", fontSize: ".6rem", color: "var(--t2)", textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600 }}>成交模型</div>
            <div>
              <div className="bt-form-label">模型</div>
              <select className="qds-select" value={form.fill_model_type} onChange={(e) => setForm((f) => ({ ...f, fill_model_type: e.target.value }))}>
                {FILL_MODEL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            {form.fill_model_type === "default" && (
              <>
                <div>
                  <div className="bt-form-label">限价单成交概率</div>
                  <input className="qds-input" type="text" value={form.prob_fill_on_limit} onChange={(e) => setForm((f) => ({ ...f, prob_fill_on_limit: e.target.value }))} placeholder="1.0" />
                </div>
                <div>
                  <div className="bt-form-label">滑点概率</div>
                  <input className="qds-input" type="text" value={form.prob_slippage} onChange={(e) => setForm((f) => ({ ...f, prob_slippage: e.target.value }))} placeholder="0.0" />
                </div>
              </>
            )}
            <div style={{ fontFamily: "var(--font-d)", fontSize: ".6rem", color: "var(--t3)", marginTop: "auto" }}>
              {FILL_MODEL_OPTIONS.find((o) => o.value === form.fill_model_type)?.hint ?? ""}
            </div>
          </div>
        </div>
      </div>

      {/* Section 3: Param Override */}
      <div className="bt-form-section">
        <div className="qds-section-label">
          策略参数覆盖
          <span style={{ fontWeight: 400, color: "var(--t2)", letterSpacing: 0, textTransform: "none", fontSize: ".55rem" }}>· 留空使用默认值</span>
        </div>
        <div className="bt-po">
          <div className="bt-po-head">
            <span>参数列表 <span style={{ color: "var(--t2)", fontWeight: 400 }}>· {strategyParams.length > 0 ? `${strategyParams.length} 个参数` : "选择策略后显示"}</span></span>
            {strategyParams.length > 0 && (
              <button className="bt-po-toggle" onClick={() => setParamsExpanded(!paramsExpanded)}>
                {paramsExpanded ? "收起 ▴" : "展开全部 ▾"}
              </button>
            )}
          </div>
          <div className={`bt-po-body ${paramsExpanded ? "open" : ""}`}>
            {strategyParams.map((p) => (
              <div key={p.name} className="bt-po-row">
                <div className="bt-po-name">
                  {p.name}
                  <span style={{ fontSize: ".55rem", color: typeColors[p.type] || "var(--t2)", marginLeft: ".2rem" }}>{p.type}</span>
                </div>
                <div className="bt-po-default">默认: {String(p.default ?? "")}</div>
                <div>
                  <input
                    className="bt-po-input"
                    placeholder={String(p.default ?? "")}
                    value={paramOverrides[p.name] ?? ""}
                    onChange={(e) => setParamOverrides((prev) => ({ ...prev, [p.name]: e.target.value }))}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Section 4: Advanced */}
      <div className="bt-form-section">
        <div className="qds-section-label">高级选项</div>
        <div className="bt-form-row">
          <div className="bt-form-group">
            <div className="bt-form-label">预热周期 (bars)</div>
            <input className="qds-input" type="text" value={form.warmup_bars} onChange={(e) => setForm((f) => ({ ...f, warmup_bars: e.target.value }))} placeholder="e.g. 200" />
            <div className="bt-form-hint">策略初始化需要的最少历史数据</div>
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">标签</div>
            <input className="qds-input" type="text" value={form.tags} onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))} placeholder="e.g. experiment-01" />
            <div className="bt-form-hint">可选，用于标记和筛选</div>
          </div>
        </div>
      </div>

      {/* Submit bar */}
      <div className="bt-submit-bar bt-form-section">
        <div style={{ display: "flex", flexDirection: "column", gap: ".25rem" }}>
          <div className="bt-submit-est">
            预估运行时间 <span style={{ color: "var(--acc)" }}>{estimate?.estimated_label ?? "—"}</span>
            {estimate?.total_bars != null && ` · 约 ${(estimate.total_bars / 1_000_000).toFixed(1)}M bars`}
          </div>
          {(submitError || submitAction.error) && (
            <div style={{ fontSize: ".72rem", color: "var(--dan)" }}>
              {submitError ?? submitAction.error}
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: ".5rem" }}>
          <button className="bt-btn-cancel" onClick={onCancel}>取消</button>
          <button
            className="bt-btn-submit"
            disabled={submitAction.state === "loading" || submitAction.state === "success"}
            onClick={handleSubmit}
            style={submitAction.state === "error" ? { background: "var(--dan)" } : undefined}
          >
            {submitAction.state === "loading" ? "提交中..." : submitAction.state === "success" ? "✓ 已加入队列" : "▶ 提交回测"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

const DETAIL_TABS = [
  { key: "overview", label: "Overview" },
  { key: "performance", label: "Performance" },
  { key: "trades", label: "Trades" },
  { key: "robustness", label: "Robustness" },
  { key: "tearsheet", label: "Report" },
  { key: "tradelog", label: "Trade Log" },
  { key: "reports", label: "Data Tables" },
];

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [view, setView] = useState<"list" | "create" | "detail">("list");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tradeLog, setTradeLog] = useState<TradeLogEntry[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [activeTab, setActiveTab] = useState("overview");
  const [curPage, setCurPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const wsMsg = useWsEvent("backtest.progress");
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});
  const [progressDetailMap, setProgressDetailMap] = useState<Record<string, {
    elapsed_secs?: number;
    eta_secs?: number;
    total_bars?: number;
    processed_bars?: number;
    bars_per_sec?: number;
    trades?: number;
    message?: string;
  }>>({});

  const resultCacheRef = useRef<Record<string, BacktestResult>>({});
  const contentRef = useRef<HTMLDivElement>(null);

  // Apply WS progress updates
  useEffect(() => {
    if (!wsMsg) return;
    const raw = (wsMsg.data ?? wsMsg) as Record<string, unknown>;
    const run_id = raw.run_id as string;
    const pct = raw.pct as number;
    if (run_id) {
      setProgressMap((prev) => ({ ...prev, [run_id]: pct }));
      setProgressDetailMap((prev) => ({
        ...prev,
        [run_id]: {
          elapsed_secs: raw.elapsed_secs as number | undefined,
          eta_secs: raw.eta_secs as number | undefined,
          total_bars: raw.total_bars as number | undefined,
          processed_bars: raw.processed_bars as number | undefined,
          bars_per_sec: raw.bars_per_sec as number | undefined,
          trades: raw.trades as number | undefined,
          message: raw.message as string | undefined,
        },
      }));
      setRuns((prev) =>
        prev.map((r) =>
          r.run_id === run_id && r.status !== "running"
            ? { ...r, status: "running" }
            : r
        )
      );
    }
  }, [wsMsg]);

  // Load runs list
  const loadRuns = useCallback(async () => {
    try {
      const data = await apiGet<{ runs: BacktestRunSummary[]; total: number }>("/api/backtest/runs?limit=100");
      if (data) setRuns(data.runs ?? []);
    } catch {
      // ignore
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRuns();
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, [loadRuns]);

  // Load strategies
  useEffect(() => {
    apiGet<StrategyInfo[]>("/api/strategies")
      .then((d) => d && setStrategies(d))
      .catch(() => {});
  }, []);

  // Load result when run selected (for trade log)
  useEffect(() => {
    if (!selectedRunId) return;
    const cached = resultCacheRef.current[selectedRunId];
    if (cached) {
      setTradeLog(cached.trade_log ?? []);
      return;
    }
    const run = runs.find((r) => r.run_id === selectedRunId);
    if (run?.status !== "completed") return;

    apiGet<BacktestResult>(`/api/backtest/${selectedRunId}/result`)
      .then((data) => {
        if (data) {
          resultCacheRef.current[selectedRunId] = data;
          setTradeLog(data.trade_log ?? []);
        }
      })
      .catch(() => {});
  }, [selectedRunId, runs]);

  const handleViewDetail = (runId: string) => {
    setSelectedRunId(runId);
    setActiveTab("overview");
    setView("detail");
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleBack = () => {
    setView("list");
    setSelectedRunId(null);
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleToggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const handleGoCreate = () => {
    setView("create");
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleBackFromCreate = () => {
    setView("list");
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const selectedRun = runs.find((r) => r.run_id === selectedRunId) ?? null;

  // Split runs into active / history
  const activeRuns = runs.filter((r) => r.status === "running" || r.status === "queued");
  const historyRuns = runs.filter((r) => r.status !== "running" && r.status !== "queued");
  const totalHistoryPages = Math.max(1, Math.ceil(historyRuns.length / pageSize));
  const safePage = Math.min(curPage, totalHistoryPages);
  const historySlice = historyRuns.slice((safePage - 1) * pageSize, safePage * pageSize);

  // Summary counts
  const statusCounts: Record<string, number> = {};
  for (const r of runs) statusCounts[r.status] = (statusCounts[r.status] ?? 0) + 1;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-1 overflow-y-auto" ref={contentRef} style={{ padding: "0 2rem 4rem" }}>

        {/* ===== LIST VIEW ===== */}
        {view === "list" && (
          <div style={{ marginTop: "1.25rem" }}>
            {/* Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1.5rem" }}>
              <div>
                <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--t0)", marginBottom: ".2rem" }}>回测管理</div>
                <div style={{ fontSize: ".75rem", color: "var(--t2)" }}>
                  {runs.length} 个回测任务
                </div>
              </div>
              <div style={{ display: "flex", gap: ".4rem" }}>
                <button
                  onClick={() => loadRuns()}
                  style={{ fontFamily: "var(--font-d)", fontSize: ".7rem", padding: ".3rem .65rem", borderRadius: "var(--rs)", border: "1px solid var(--bd)", background: "none", color: "var(--t1)", cursor: "pointer", transition: "all 150ms", display: "flex", alignItems: "center", gap: ".3rem" }}
                  title="刷新"
                >
                  <RefreshCw className="w-3 h-3" />
                </button>
                <button
                  onClick={handleGoCreate}
                  style={{ fontFamily: "var(--font-d)", fontSize: ".72rem", padding: ".4rem .85rem", borderRadius: "var(--rs)", border: "1px solid var(--acc)", background: "var(--acc-d)", color: "var(--acc)", cursor: "pointer", transition: "all 150ms", display: "flex", alignItems: "center", gap: ".3rem" }}
                >
                  <Plus className="w-3 h-3" /> 创建回测
                </button>
              </div>
            </div>

            {/* Summary strip */}
            {runs.length > 0 && (
              <div className="bt-summary">
                {(() => {
                  const items: { key: string; color: string; label: string }[] = [];
                  if (statusCounts.running) items.push({ key: "running", color: "var(--info)", label: `${statusCounts.running} Running` });
                  if (statusCounts.completed) items.push({ key: "done", color: "var(--suc)", label: `${statusCounts.completed} Done` });
                  if (statusCounts.failed) items.push({ key: "fail", color: "var(--dan)", label: `${statusCounts.failed} Failed` });
                  if (statusCounts.queued) items.push({ key: "queue", color: "var(--t3)", label: `${statusCounts.queued} Queued` });
                  if (statusCounts.cancelled) items.push({ key: "cancel", color: "var(--t3)", label: `${statusCounts.cancelled} Cancelled` });
                  return items.map((item) => (
                    <div key={item.key} className="bt-summary-item">
                      <div className="bt-summary-dot" style={{ background: item.color }} />
                      <span style={{ color: item.color }}>{item.label}</span>
                    </div>
                  ));
                })()}
              </div>
            )}

            {/* Loading skeleton */}
            {runsLoading ? (
              <div className="bt-list">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="bt-row" style={{ padding: ".75rem 1rem" }}>
                    <Skeleton className="h-10 w-full rounded" />
                  </div>
                ))}
              </div>
            ) : runs.length === 0 ? (
              <div className="bt-list">
                <EmptyState
                  variant="first-use"
                  icon={<span className="text-muted-foreground">⧖</span>}
                  title="还没有回测记录"
                  description="创建你的第一个回测，在历史数据上验证策略表现"
                  action={{ label: "+ 创建回测", onClick: handleGoCreate }}
                  hint="支持 Bar 和 Tick 粒度"
                />
              </div>
            ) : (
              <>
                {/* ZONE 1: Active tasks */}
                {activeRuns.length > 0 && (
                  <div className="bt-list">
                    {activeRuns.map((run) => (
                      <RunRow
                        key={run.run_id}
                        run={run}
                        progress={progressMap[run.run_id] ?? run.progress_pct ?? null}
                        progressDetail={progressDetailMap[run.run_id]}
                        expandedId={expandedId}
                        onToggleExpand={handleToggleExpand}
                        onViewDetail={handleViewDetail}
                      />
                    ))}
                  </div>
                )}

                {/* ZONE 2: History */}
                {historyRuns.length > 0 && (
                  <div style={{ marginTop: "1.5rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".65rem" }}>
                      <div className="qds-section-label" style={{ marginBottom: 0 }}>历史记录</div>
                      <div className="bt-pager-size">
                        <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setCurPage(1); }}>
                          <option value={10}>10 条/页</option>
                          <option value={20}>20 条/页</option>
                          <option value={50}>50 条/页</option>
                        </select>
                      </div>
                    </div>
                    <div className="bt-list">
                      {historySlice.map((run) => (
                        <HistoryRow
                          key={run.run_id}
                          run={run}
                          expanded={expandedId === run.run_id}
                          onToggleExpand={handleToggleExpand}
                          onViewDetail={handleViewDetail}
                        />
                      ))}
                    </div>
                    {totalHistoryPages > 1 && (
                      <Pager
                        curPage={safePage}
                        totalPages={totalHistoryPages}
                        total={historyRuns.length}
                        pageSize={pageSize}
                        onPageChange={(p) => setCurPage(p)}
                      />
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* ===== CREATE VIEW ===== */}
        {view === "create" && (
          <CreateFormView
            strategies={strategies}
            onSubmit={async () => { await loadRuns(); }}
            onCancel={handleBackFromCreate}
          />
        )}

        {/* ===== DETAIL VIEW ===== */}
        {view === "detail" && selectedRun && (
          <div>
            {/* Detail top bar */}
            <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginTop: "1.25rem", marginBottom: "1rem", paddingBottom: "1rem", borderBottom: "1px solid var(--bd)" }}>
              <button
                className="bt-view-btn"
                onClick={handleBack}
                style={{ fontSize: ".72rem", padding: ".35rem .7rem" }}
              >
                <span style={{ transition: "transform 150ms" }}>&larr;</span> 返回
              </button>
              <div>
                <div style={{ fontFamily: "var(--font-d)", fontSize: "1rem", fontWeight: 600, display: "flex", alignItems: "center", gap: ".5rem" }}>
                  {selectedRun.strategy_name}
                  <CopyableId runId={selectedRun.run_id} />
                </div>
                <div style={{ fontSize: ".72rem", color: "var(--t2)" }}>
                  {(() => {
                    const syms = selectedRun.symbol.split(",").map((s) => s.trim()).filter(Boolean);
                    return syms.length <= 3 ? syms.join(", ") : `${syms.slice(0, 3).join(", ")} +${syms.length - 3}`;
                  })()} · {selectedRun.interval} · {selectedRun.start_date?.slice(0, 10)} → {selectedRun.end_date?.slice(0, 10)}
                </div>
              </div>
              <div style={{ marginLeft: "auto", display: "flex", gap: ".4rem" }}>
                <button className="bt-act-btn">导出</button>
                <button className="bt-act-btn">克隆</button>
                <button className="bt-act-btn" style={{ color: "var(--dan)" }}>删除</button>
              </div>
            </div>

            {/* Pill tab bar */}
            <div className="bt-tab-bar-wrap" style={{ margin: "0 -2rem", paddingLeft: "2rem", paddingRight: "2rem" }}>
              <div className="bt-tab-bar">
                {DETAIL_TABS.map((tab) => (
                  <button
                    key={tab.key}
                    className={`bt-dtab ${activeTab === tab.key ? "active" : ""}`}
                    onClick={() => setActiveTab(tab.key)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Tab content */}
            <div className="mt-4">
              {activeTab === "overview" && (
                selectedRun.status === "completed" ? (
                  <OverviewTab runId={selectedRunId!} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} message={progressDetailMap[selectedRunId!]?.message} />
                )
              )}
              {activeTab === "performance" && (
                selectedRun.status === "completed" && selectedRunId ? (
                  <PerformanceTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} message={progressDetailMap[selectedRunId!]?.message} />
                )
              )}
              {activeTab === "trades" && (
                selectedRun.status === "completed" && selectedRunId ? (
                  <TradesTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} message={progressDetailMap[selectedRunId!]?.message} />
                )
              )}
              {activeTab === "robustness" && (
                selectedRun.status === "completed" && selectedRunId ? (
                  <RobustnessTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} message={progressDetailMap[selectedRunId!]?.message} />
                )
              )}
              {activeTab === "tearsheet" && (
                selectedRun.status === "completed" ? (
                  <TearsheetTab runId={selectedRunId!} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} message={progressDetailMap[selectedRunId!]?.message} />
                )
              )}
              {activeTab === "tradelog" && (
                selectedRun.status === "completed" ? (
                  <TradeLogTab tradeLog={tradeLog} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} message={progressDetailMap[selectedRunId!]?.message} />
                )
              )}
              {activeTab === "reports" && (
                selectedRun.status === "completed" ? (
                  <ReportsTab runId={selectedRunId!} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} message={progressDetailMap[selectedRunId!]?.message} />
                )
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
