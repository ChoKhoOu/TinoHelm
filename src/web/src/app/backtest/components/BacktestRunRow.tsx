"use client";

import { ChevronDown } from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { BacktestCopyableId } from "./BacktestRunningPlaceholder";
import {
  ACCENT_BG_MAP,
  STATUS_PILL_MAP,
  VIEW_BTN_CLS,
} from "./backtestStyles";
import type { BacktestProgressDetail, BacktestRunSummary } from "./BacktestListView";

/* ------------------------------------------------------------------ */
/*  Formatting helpers                                                 */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/*  BacktestRunRow (active runs: running / queued)                     */
/* ------------------------------------------------------------------ */

interface BacktestRunRowProps {
  run: BacktestRunSummary;
  progress: number | null;
  progressDetail?: BacktestProgressDetail;
  expandedId: string | null;
  onToggleExpand: (id: string) => void;
  onViewDetail: (id: string) => void;
}

export function BacktestRunRow({ run, progress, progressDetail, expandedId, onToggleExpand, onViewDetail }: BacktestRunRowProps) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isRunning = run.status === "running";
  const isQueued = run.status === "queued";
  const isDone = run.status === "completed";
  const isFailed = run.status === "failed";
  const isExpandable = isRunning || isQueued;
  const isExpanded = expandedId === run.run_id;
  const pct = progress ?? 0;

  const statusKey =
    run.status === "completed"
      ? "done"
      : run.status === "running"
      ? "run"
      : run.status === "failed"
      ? "fail"
      : "queue";

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

  const accentCls = ACCENT_BG_MAP[statusKey] ?? "bg-qds-t3";
  const statusPillCls = STATUS_PILL_MAP[statusKey] ?? "bg-secondary text-muted-foreground";

  return (
    <div className="relative bg-card border-b border-border last:border-b-0 transition-colors hover:bg-secondary">
      <div
        className="grid items-center cursor-pointer"
        style={{ gridTemplateColumns: "3px 1fr auto auto auto" }}
        onClick={handleRowClick}
      >
        <div className={`self-stretch ${accentCls}`} />
        <div className="flex flex-col gap-1 px-3 py-3">
          <div className="flex items-center gap-2 font-mono text-[0.82rem] font-medium">
            {run.strategy_name}
            {(() => {
              const syms = run.symbol.split(",").map((s) => s.trim()).filter(Boolean);
              if (syms.length <= 2) {
                return <span className="text-[0.7rem] font-normal text-muted-foreground">{syms.join(", ")}</span>;
              }
              return (
                <span className="text-[0.7rem] font-normal text-muted-foreground">
                  {syms.slice(0, 2).join(", ")}{" "}
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger className="text-[0.65rem] text-muted-foreground cursor-default">
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
          <div className="flex items-center gap-2 font-mono text-[0.68rem] text-muted-foreground">
            <BacktestCopyableId runId={run.run_id} />
            {run.interval} · {dateRange}
          </div>
        </div>

        <div className="px-2 py-3">
          <span className={`inline-flex items-center gap-1.5 font-mono text-[0.68rem] font-medium px-2 py-0.5 rounded-full ${statusPillCls}`}>
            {run.status === "running" && (
              <span className="relative w-1.5 h-1.5 rounded-full bg-primary shrink-0">
                <span className="absolute inset-[-3px] rounded-full border-[1.5px] border-primary animate-qds-pulse opacity-0" />
              </span>
            )}
            {run.status === "completed" && "✓ "}
            {run.status === "failed" && "✕ "}
            {run.status === "queued" && "◦ "}
            {statusLabel[run.status] ?? run.status}
          </span>
        </div>

        <div className="px-2 py-3 text-right font-mono text-[0.75rem] min-w-[80px]">
          {isRunning && (
            <span className="text-primary">{pct}%</span>
          )}
          {isQueued && (
            <span className="inline-flex items-center gap-[3px] text-qds-t3 font-mono text-[0.65rem]">
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
          {isFailed && <span className="text-destructive text-[0.68rem]">{run.error ? run.error.slice(0, 16) : "Error"}</span>}
        </div>

        <div className="flex items-center pr-3 py-3 gap-1">
          {isExpandable && (
            <span
              className="inline-block text-[0.68rem] text-muted-foreground cursor-pointer transition-transform duration-300"
              style={{ transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)" }}
            >
              <ChevronDown className="w-3.5 h-3.5" />
            </span>
          )}
          {(isDone || isFailed) && (
            <button
              className={VIEW_BTN_CLS}
              onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}
            >
              View <span className="transition-transform">&rarr;</span>
            </button>
          )}
        </div>
      </div>

      {isRunning && !isExpanded && (
        <div className="relative h-[3px] overflow-hidden">
          <div
            className="h-full bg-primary transition-[width] duration-[1.5s] ease-qds"
            style={{ width: `${pct}%` }}
          />
          <div className="absolute inset-0 animate-qds-shimmer pointer-events-none">
            <div className="h-full w-full bg-gradient-to-r from-transparent via-white/35 to-transparent" />
          </div>
        </div>
      )}

      {isExpandable && (
        <div
          className="overflow-hidden bg-input border-t transition-[max-height] duration-[400ms] ease-qds"
          style={{ maxHeight: isExpanded ? 400 : 0, borderTopWidth: isExpanded ? 1 : 0 }}
        >
          <div className="p-4 pl-[calc(1rem+3px)]">
            {isRunning && (
              <>
                <div className="relative h-1.5 rounded bg-secondary overflow-hidden mb-3">
                  <div
                    className="h-full rounded bg-primary transition-[width] duration-[1.5s] ease-qds"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <div className="grid grid-cols-4 gap-2">
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Progress</div>
                    <div className="font-mono text-[0.75rem] font-medium text-primary">{pct}%</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Elapsed</div>
                    <div className="font-mono text-[0.75rem] font-medium">{fmtSecs(progressDetail?.elapsed_secs)}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">ETA</div>
                    <div className="font-mono text-[0.75rem] font-medium">{progressDetail?.eta_secs != null ? `~${fmtSecs(progressDetail.eta_secs)}` : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Speed</div>
                    <div className="font-mono text-[0.75rem] font-medium">{progressDetail?.bars_per_sec != null ? `${fmtBars(progressDetail.bars_per_sec)}/s` : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Processed</div>
                    <div className="font-mono text-[0.75rem] font-medium">
                      {progressDetail?.processed_bars != null
                        ? `${fmtBars(progressDetail.processed_bars)}${progressDetail.total_bars != null ? ` / ${fmtBars(progressDetail.total_bars)}` : ""}`
                        : "—"}
                    </div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Trades</div>
                    <div className="font-mono text-[0.75rem] font-medium">{progressDetail?.trades != null ? progressDetail.trades : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Memory</div>
                    <div className="font-mono text-[0.75rem] font-medium text-muted-foreground">—</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">CPU</div>
                    <div className="font-mono text-[0.75rem] font-medium text-muted-foreground">—</div>
                  </div>
                </div>
              </>
            )}
            {isQueued && (
              <div>
                <div className="grid gap-4" style={{ gridTemplateColumns: "1.5fr 1fr" }}>
                  <div>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground mb-1.5">Preview</div>
                    <div className="rounded-lg bg-secondary h-[72px]" />
                  </div>
                  <div>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground mb-1.5">Config</div>
                    <div className="flex flex-col gap-1.5">
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary w-[55px] h-[11px]" />
                        <div className="rounded bg-secondary flex-1 h-[11px]" />
                      </div>
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary w-[65px] h-[11px]" />
                        <div className="rounded bg-secondary flex-1 h-[11px]" />
                      </div>
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary w-[45px] h-[11px]" />
                        <div className="rounded bg-secondary flex-1 h-[11px]" />
                      </div>
                    </div>
                  </div>
                </div>
                <div className="mt-3 font-mono text-[0.7rem] text-muted-foreground">
                  Estimated start in <span className="text-primary">~12 min</span>
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
/*  BacktestHistoryRow (completed / failed, simplified)                */
/* ------------------------------------------------------------------ */

interface BacktestHistoryRowProps {
  run: BacktestRunSummary;
  expanded: boolean;
  onToggleExpand: (id: string) => void;
  onViewDetail: (id: string) => void;
}

export function BacktestHistoryRow({ run, expanded, onToggleExpand, onViewDetail }: BacktestHistoryRowProps) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isDone = run.status === "completed";
  const statusKey = isDone ? "done" : "fail";
  const s = run.result_summary;

  const accentCls = ACCENT_BG_MAP[statusKey] ?? "bg-qds-t3";
  const statusPillCls = STATUS_PILL_MAP[statusKey] ?? "bg-secondary text-muted-foreground";

  return (
    <div className="relative bg-card border-b border-border last:border-b-0 transition-colors hover:bg-secondary">
      <div
        className="grid items-center cursor-pointer"
        style={{ gridTemplateColumns: "3px 1fr auto auto auto" }}
        onClick={() => onToggleExpand(run.run_id)}
      >
        <div className={`self-stretch ${accentCls}`} />
        <div className="flex flex-col gap-1 px-3 py-3">
          <div className="flex items-center gap-2 font-mono text-[0.82rem] font-medium">
            {run.strategy_name}
            {(() => {
              const syms = run.symbol.split(",").map((x) => x.trim()).filter(Boolean);
              if (syms.length <= 2) {
                return <span className="text-[0.7rem] font-normal text-muted-foreground">{syms.join(", ")}</span>;
              }
              return (
                <span className="text-[0.7rem] font-normal text-muted-foreground">
                  {syms.slice(0, 2).join(", ")} +{syms.length - 2}
                </span>
              );
            })()}
          </div>
          <div className="flex items-center gap-2 font-mono text-[0.68rem] text-muted-foreground">
            <BacktestCopyableId runId={run.run_id} />
            {run.interval} · {dateRange}
          </div>
        </div>

        <div className="px-2 py-3">
          <span className={`inline-flex items-center gap-1.5 font-mono text-[0.68rem] font-medium px-2 py-0.5 rounded-full ${statusPillCls}`}>
            {isDone ? "✓ Done" : "✕ Failed"}
          </span>
        </div>

        <div className="px-2 py-3 text-right font-mono text-[0.75rem] min-w-[80px]">
          {isDone && s?.total_pnl != null ? (
            <span className={s.total_pnl >= 0 ? "text-qds-success" : "text-destructive"}>
              {s.total_pnl >= 0 ? "+" : "-"}${Math.abs(s.total_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          ) : isDone ? (
            <span className="text-qds-success">Completed</span>
          ) : (
            <span className="text-destructive text-[0.68rem]">{run.error ? run.error.slice(0, 24) : "Error"}</span>
          )}
        </div>

        <div className="flex items-center pr-3 py-3 gap-1">
          <span className="inline-block text-[0.68rem] text-qds-t3 cursor-pointer transition-colors leading-none pl-1 pr-2 py-3">▾</span>
        </div>
      </div>

      <div
        className="overflow-hidden bg-input border-t transition-[max-height] duration-[400ms] ease-qds"
        style={{ maxHeight: expanded ? 400 : 0, borderTopWidth: expanded ? 1 : 0 }}
      >
        <div className="p-4 pl-[calc(1rem+3px)]">
          {isDone && s ? (
            <>
              <div className="grid grid-cols-4 gap-2 mb-3">
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Sharpe</div>
                  <div className={`font-mono text-[0.75rem] font-medium ${(s.sharpe_ratio ?? 0) >= 1 ? "text-qds-success" : (s.sharpe_ratio ?? 0) >= 0 ? "" : "text-destructive"}`}>
                    {s.sharpe_ratio?.toFixed(2) ?? "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Win Rate</div>
                  <div className="font-mono text-[0.75rem] font-medium">{s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : "—"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Profit Factor</div>
                  <div className={`font-mono text-[0.75rem] font-medium ${(s.profit_factor ?? 0) >= 1.5 ? "text-qds-success" : ""}`}>
                    {s.profit_factor?.toFixed(2) ?? "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Max DD</div>
                  <div className="font-mono text-[0.75rem] font-medium text-destructive">{s.max_drawdown != null ? `${(s.max_drawdown * 100).toFixed(1)}%` : "—"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Calmar</div>
                  <div className={`font-mono text-[0.75rem] font-medium ${(s.calmar_ratio ?? 0) >= 1 ? "text-qds-success" : (s.calmar_ratio ?? 0) >= 0 ? "" : "text-destructive"}`}>
                    {s.calmar_ratio?.toFixed(2) ?? "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Trades</div>
                  <div className="font-mono text-[0.75rem] font-medium">{s.total_trades?.toLocaleString() ?? "—"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">PnL</div>
                  <div className={`font-mono text-[0.75rem] font-medium ${(s.total_pnl ?? 0) >= 0 ? "text-qds-success" : "text-destructive"}`}>
                    {s.total_pnl != null ? `${s.total_pnl >= 0 ? "+" : "-"}$${Math.abs(s.total_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}
                  </div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Return</div>
                  <div className={`font-mono text-[0.75rem] font-medium ${(s.total_return_pct ?? 0) >= 0 ? "text-qds-success" : "text-destructive"}`}>
                    {s.total_return_pct != null ? `${s.total_return_pct >= 0 ? "+" : ""}${s.total_return_pct.toFixed(1)}%` : "—"}
                  </div>
                </div>
              </div>
              <div className="flex justify-end">
                <button className={VIEW_BTN_CLS} onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}>
                  查看完整报告 <span>&rarr;</span>
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-2 mb-3">
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">错误类型</div>
                  <div className="font-mono text-[0.75rem] font-medium text-destructive">{run.error ?? "Unknown error"}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">品种</div>
                  <div className="font-mono text-[0.75rem] font-medium">{run.symbol}</div>
                </div>
                <div className="flex flex-col gap-0.5">
                  <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">策略</div>
                  <div className="font-mono text-[0.75rem] font-medium">{run.strategy_name}</div>
                </div>
              </div>
              <div className="flex justify-end gap-2">
                <button className={VIEW_BTN_CLS} onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}>
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
