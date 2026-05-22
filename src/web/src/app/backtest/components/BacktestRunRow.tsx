"use client";

import { ChevronDown, RotateCcw } from "lucide-react";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { BacktestCopyableId } from "./BacktestRunningPlaceholder";
import { StatusBadge } from "@/components/qds/status-badge";
import { ShimmerBar } from "@/components/qds/shimmer-bar";
import {
  ACCENT_BG_MAP,
  VIEW_BTN_CLS,
} from "./backtestStyles";
import type { BacktestProgressDetail, BacktestRunSummary } from "./BacktestListView";

/* ------------------------------------------------------------------ */
/*  RingProgress — SVG ring with percentage text                       */
/* ------------------------------------------------------------------ */

const RING_R = 18;
const RING_C = 2 * Math.PI * RING_R;

function RingProgress({ pct }: { pct: number }) {
  const clampedPct = Math.min(100, Math.max(0, pct));
  const dashOffset = RING_C * (1 - clampedPct / 100);
  return (
    <svg
      data-ring-progress
      viewBox="0 0 44 44"
      width={44}
      height={44}
      style={{ flexShrink: 0 }}
    >
      {/* track */}
      <circle
        cx="22"
        cy="22"
        r={RING_R}
        fill="none"
        stroke="var(--bd)"
        strokeWidth="3"
      />
      {/* progress */}
      <circle
        cx="22"
        cy="22"
        r={RING_R}
        fill="none"
        stroke="var(--info)"
        strokeWidth="3"
        strokeDasharray={RING_C}
        strokeDashoffset={dashOffset}
        transform="rotate(-90 22 22)"
        style={{ transition: "stroke-dashoffset 0.3s ease" }}
        className="motion-reduce:transition-none"
      />
      {/* center text */}
      <text
        x="22"
        y="22"
        textAnchor="middle"
        dominantBaseline="central"
        fontSize="10"
        fill="var(--t0)"
      >
        {clampedPct}%
      </text>
    </svg>
  );
}

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

/** Format PnL with sign prefix, $ symbol, and thousands separator. */
function fmtPnl(pnl: number | null | undefined): string {
  if (pnl == null) return "—";
  const abs = Math.abs(pnl).toLocaleString(undefined, { maximumFractionDigits: 0 });
  return pnl >= 0 ? `+$${abs}` : `-$${abs}`;
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
  onCancelRun?: (runId: string) => void;
  isWsStale?: boolean;
}

export function BacktestRunRow({ run, progress, progressDetail, expandedId, onToggleExpand, onViewDetail, onCancelRun, isWsStale = false }: BacktestRunRowProps) {
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

  const handleRowClick = () => {
    if (isExpandable) {
      onToggleExpand(run.run_id);
    } else {
      onViewDetail(run.run_id);
    }
  };

  const accentCls = ACCENT_BG_MAP[statusKey] ?? "bg-qds-t3";

  return (
    <div className="relative bg-card border-b border-border last:border-b-0 transition-colors hover:bg-secondary">
      {/* 10-col responsive grid:
          col1=3px stripe | col2=strategy+symbol (1fr) | col3=run_id (lg+) |
          col4=date range | col5=status badge | col6=total return |
          col7=sharpe (xl+) | col8=winrate (xl+) | col9=pnl | col10=actions */}
      <div
        className="grid items-center cursor-pointer"
        style={{ gridTemplateColumns: "3px 1fr auto auto auto auto auto auto auto auto" }}
        onClick={handleRowClick}
      >
        {/* col1: accent stripe */}
        <div className={`self-stretch ${accentCls}`} />

        {/* col2: strategy + symbol (always visible) */}
        <div className="flex flex-col gap-1 px-3 py-3 min-w-0">
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
          {/* sub-row: interval · dateRange; on <lg also show run_id short */}
          <div className="flex items-center gap-2 font-mono text-[0.68rem] text-muted-foreground">
            <span className="lg:hidden text-qds-t3">{run.run_id.slice(0, 8)} ·</span>
            {run.interval ? `${run.interval} · ${dateRange}` : dateRange}
          </div>
        </div>

        {/* col3: run_id — hidden on <lg, visible on lg+ */}
        <div className="hidden lg:flex items-center px-2 py-3">
          <BacktestCopyableId runId={run.run_id} />
        </div>

        {/* col4: date range */}
        <div className="px-2 py-3 font-mono text-[0.68rem] text-muted-foreground whitespace-nowrap">
          {dateRange}
        </div>

        {/* col5: status badge */}
        <div className="px-2 py-3" data-meta-cell>
          <StatusBadge status={run.status} locale="en" />
        </div>

        {/* col6: total return pct */}
        <div className="px-2 py-3 text-right font-mono text-[0.75rem]">
          {isDone && run.result_summary?.total_return_pct != null ? (
            <span className={run.result_summary.total_return_pct >= 0 ? "text-qds-success" : "text-destructive"}>
              {run.result_summary.total_return_pct >= 0 ? "+" : ""}{run.result_summary.total_return_pct.toFixed(1)}%
            </span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          )}
        </div>

        {/* col7: sharpe — hidden on <xl */}
        <div className="hidden xl:flex items-center px-2 py-3 text-right font-mono text-[0.75rem]">
          {isDone && run.result_summary?.sharpe_ratio != null ? (
            <span className={(run.result_summary.sharpe_ratio ?? 0) >= 1 ? "text-qds-success" : (run.result_summary.sharpe_ratio ?? 0) >= 0 ? "text-foreground" : "text-destructive"}>
              {run.result_summary.sharpe_ratio.toFixed(2)}
            </span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          )}
        </div>

        {/* col8: win rate — hidden on <xl */}
        <div className="hidden xl:flex items-center px-2 py-3 text-right font-mono text-[0.75rem]">
          {isDone && run.result_summary?.win_rate != null ? (
            <span>{(run.result_summary.win_rate * 100).toFixed(1)}%</span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          )}
        </div>

        {/* col9: PnL */}
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
              {fmtPnl(run.result_summary.total_pnl)}
            </span>
          ) : isDone ? (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          ) : null}
          {isFailed && <span className="text-destructive text-[0.68rem]">{run.error ? run.error.slice(0, 16) : "Error"}</span>}
        </div>

        {/* col10: actions */}
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
          {!isWsStale && (
            <div className="absolute inset-0 animate-qds-shimmer pointer-events-none">
              <div className="h-full w-full bg-gradient-to-r from-transparent via-white/35 to-transparent" />
            </div>
          )}
        </div>
      )}

      {isExpandable && (
        <div
          className="overflow-hidden bg-input border-t transition-[max-height] duration-[400ms] ease-qds"
          style={{ maxHeight: isExpanded ? 400 : 0, borderTopWidth: isExpanded ? 1 : 0 }}
        >
          <div className="p-4 pl-[calc(1rem+3px)]" data-ws-stale={isWsStale ? "true" : "false"}>
            {isRunning && (
              <>
                {/* Top row: ring + shimmer bar + cancel */}
                <div className="flex items-center gap-3 mb-3">
                  <RingProgress pct={pct} />
                  <div className="flex-1">
                    <ShimmerBar progress={pct} height="md" active={!isWsStale} variant="accent" />
                  </div>
                  {onCancelRun && (
                    <button
                      className={`${VIEW_BTN_CLS} !text-destructive hover:!border-destructive hover:!bg-destructive/10`}
                      onClick={(e) => { e.stopPropagation(); onCancelRun(run.run_id); }}
                    >
                      Cancel
                    </button>
                  )}
                </div>
                {/* 6 meta cells */}
                <div className="grid grid-cols-6 gap-2">
                  <div className="flex flex-col gap-0.5" data-meta-cell>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Progress</div>
                    <div className="font-mono text-[0.75rem] font-medium text-primary flex items-center">
                      <span>{pct}%</span>
                      {isWsStale && (
                        <span className="text-qds-warning text-[0.6rem] ml-1">· 连接待恢复</span>
                      )}
                    </div>
                  </div>
                  <div className="flex flex-col gap-0.5" data-meta-cell>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Elapsed</div>
                    <div className="font-mono text-[0.75rem] font-medium">{fmtSecs(progressDetail?.elapsed_secs)}</div>
                  </div>
                  <div className="flex flex-col gap-0.5" data-meta-cell>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">ETA</div>
                    <div className="font-mono text-[0.75rem] font-medium">{progressDetail?.eta_secs != null ? `~${fmtSecs(progressDetail.eta_secs)}` : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5" data-meta-cell>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Speed</div>
                    <div className="font-mono text-[0.75rem] font-medium">{progressDetail?.bars_per_sec != null ? `${fmtBars(progressDetail.bars_per_sec)}/s` : "—"}</div>
                  </div>
                  <div className="flex flex-col gap-0.5" data-meta-cell>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Processed</div>
                    <div className="font-mono text-[0.75rem] font-medium">
                      {progressDetail?.processed_bars != null
                        ? `${fmtBars(progressDetail.processed_bars)}${progressDetail.total_bars != null ? ` / ${fmtBars(progressDetail.total_bars)}` : ""}`
                        : "—"}
                    </div>
                  </div>
                  <div className="flex flex-col gap-0.5" data-meta-cell>
                    <div className="text-[0.6rem] uppercase tracking-wider text-muted-foreground">Trades</div>
                    <div className="font-mono text-[0.75rem] font-medium">{progressDetail?.trades != null ? progressDetail.trades : "—"}</div>
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
  onRetryRun?: (run: BacktestRunSummary) => void;
}

export function BacktestHistoryRow({ run, expanded, onToggleExpand, onViewDetail, onRetryRun }: BacktestHistoryRowProps) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isDone = run.status === "completed";
  const statusKey = isDone ? "done" : "fail";
  const s = run.result_summary;

  const accentCls = ACCENT_BG_MAP[statusKey] ?? "bg-qds-t3";

  return (
    <div className="relative bg-card border-b border-border last:border-b-0 transition-colors hover:bg-secondary">
      {/* 10-col responsive grid — same structure as BacktestRunRow */}
      <div
        className="grid items-center cursor-pointer"
        style={{ gridTemplateColumns: "3px 1fr auto auto auto auto auto auto auto auto" }}
        onClick={() => onToggleExpand(run.run_id)}
      >
        {/* col1: accent stripe */}
        <div className={`self-stretch ${accentCls}`} />

        {/* col2: strategy + symbol (always visible) */}
        <div className="flex flex-col gap-1 px-3 py-3 min-w-0">
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
          {/* sub-row: interval · dateRange; on <lg also show run_id short */}
          <div className="flex items-center gap-2 font-mono text-[0.68rem] text-muted-foreground">
            <span className="lg:hidden text-qds-t3">{run.run_id.slice(0, 8)} ·</span>
            {run.interval ? `${run.interval} · ${dateRange}` : dateRange}
          </div>
        </div>

        {/* col3: run_id — hidden on <lg, visible on lg+ */}
        <div className="hidden lg:flex items-center px-2 py-3">
          <BacktestCopyableId runId={run.run_id} />
        </div>

        {/* col4: date range */}
        <div className="px-2 py-3 font-mono text-[0.68rem] text-muted-foreground whitespace-nowrap">
          {dateRange}
        </div>

        {/* col5: status badge */}
        <div className="px-2 py-3" data-meta-cell>
          <StatusBadge status={run.status} locale="en" />
        </div>

        {/* col6: total return pct */}
        <div className="px-2 py-3 text-right font-mono text-[0.75rem]">
          {isDone && s?.total_return_pct != null ? (
            <span className={s.total_return_pct >= 0 ? "text-qds-success" : "text-destructive"}>
              {s.total_return_pct >= 0 ? "+" : ""}{s.total_return_pct.toFixed(1)}%
            </span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          )}
        </div>

        {/* col7: sharpe — hidden on <xl */}
        <div className="hidden xl:flex items-center px-2 py-3 text-right font-mono text-[0.75rem]">
          {isDone && s?.sharpe_ratio != null ? (
            <span className={(s.sharpe_ratio ?? 0) >= 1 ? "text-qds-success" : (s.sharpe_ratio ?? 0) >= 0 ? "text-foreground" : "text-destructive"}>
              {s.sharpe_ratio.toFixed(2)}
            </span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          )}
        </div>

        {/* col8: win rate — hidden on <xl */}
        <div className="hidden xl:flex items-center px-2 py-3 text-right font-mono text-[0.75rem]">
          {isDone && s?.win_rate != null ? (
            <span>{(s.win_rate * 100).toFixed(1)}%</span>
          ) : (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          )}
        </div>

        {/* col9: PnL */}
        <div className="px-2 py-3 text-right font-mono text-[0.75rem] min-w-[80px]">
          {isDone && s?.total_pnl != null ? (
            <span className={s.total_pnl >= 0 ? "text-qds-success" : "text-destructive"}>
              {fmtPnl(s.total_pnl)}
            </span>
          ) : isDone ? (
            <span className="font-mono text-xs text-muted-foreground">—</span>
          ) : (
            <span className="text-destructive text-[0.68rem]">{run.error ? run.error.slice(0, 16) : "Error"}</span>
          )}
        </div>

        {/* col10: actions */}
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
                {onRetryRun && (
                  <button
                    type="button"
                    className={VIEW_BTN_CLS}
                    onClick={(e) => { e.stopPropagation(); onRetryRun(run); }}
                  >
                    <RotateCcw className="w-3 h-3" /> 重试
                  </button>
                )}
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
