"use client";

import { Plus, RefreshCw } from "lucide-react";
import { EmptyState } from "@/components/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import type { RunStatus } from "../types";
import { BacktestRunRow, BacktestHistoryRow } from "./BacktestRunRow";
import { BacktestPagination } from "./BacktestPagination";

/* ------------------------------------------------------------------ */
/*  Shared types (also consumed by hooks + row sub-components)         */
/* ------------------------------------------------------------------ */

export interface BacktestRunSummary {
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

export interface BacktestProgressDetail {
  elapsed_secs?: number;
  eta_secs?: number;
  total_bars?: number;
  processed_bars?: number;
  bars_per_sec?: number;
  trades?: number;
  message?: string;
}

/* ------------------------------------------------------------------ */
/*  BacktestListView — top-level list page composition                 */
/* ------------------------------------------------------------------ */

interface BacktestListViewProps {
  runs: BacktestRunSummary[];
  runsLoading: boolean;
  progressMap: Record<string, number>;
  progressDetailMap: Record<string, BacktestProgressDetail>;
  expandedId: string | null;
  curPage: number;
  pageSize: number;
  onRefresh: () => void;
  onGoCreate: () => void;
  onToggleExpand: (id: string) => void;
  onViewDetail: (id: string) => void;
  onPageChange: (p: number) => void;
  onPageSizeChange: (size: number) => void;
}

export function BacktestListView({
  runs,
  runsLoading,
  progressMap,
  progressDetailMap,
  expandedId,
  curPage,
  pageSize,
  onRefresh,
  onGoCreate,
  onToggleExpand,
  onViewDetail,
  onPageChange,
  onPageSizeChange,
}: BacktestListViewProps) {
  // Split runs into active / history.
  const activeRuns = runs.filter((r) => r.status === "running" || r.status === "queued");
  const historyRuns = runs.filter((r) => r.status !== "running" && r.status !== "queued");
  const totalHistoryPages = Math.max(1, Math.ceil(historyRuns.length / pageSize));
  const safePage = Math.min(curPage, totalHistoryPages);
  const historySlice = historyRuns.slice((safePage - 1) * pageSize, safePage * pageSize);

  // Summary counts.
  const statusCounts: Record<string, number> = {};
  for (const r of runs) statusCounts[r.status] = (statusCounts[r.status] ?? 0) + 1;

  return (
    <div className="mt-5">
      {/* Header */}
      <div className="flex justify-between items-start mb-6">
        <div>
          <div className="text-lg font-bold text-foreground mb-0.5">回测管理</div>
          <div className="text-[0.75rem] text-muted-foreground">
            {runs.length} 个回测任务
          </div>
        </div>
        <div className="flex gap-1.5">
          <button
            onClick={onRefresh}
            className="inline-flex items-center gap-1 font-mono text-[0.7rem] px-2.5 py-1.5 rounded-md border border-border bg-transparent text-qds-t1 cursor-pointer transition-all hover:border-qds-border-hover hover:text-foreground hover:bg-secondary"
            title="刷新"
          >
            <RefreshCw className="w-3 h-3" />
          </button>
          <button
            onClick={onGoCreate}
            className="inline-flex items-center gap-1 font-mono text-[0.72rem] px-3 py-2 rounded-md border border-primary bg-primary/15 text-primary cursor-pointer transition-all hover:bg-primary/20"
          >
            <Plus className="w-3 h-3" /> 创建回测
          </button>
        </div>
      </div>

      {/* Summary strip */}
      {runs.length > 0 && (
        <div className="flex gap-6 mb-5 font-mono text-[0.75rem]">
          {(() => {
            const items: { key: string; color: string; label: string }[] = [];
            if (statusCounts.running) items.push({ key: "running", color: "var(--info)", label: `${statusCounts.running} Running` });
            if (statusCounts.completed) items.push({ key: "done", color: "var(--suc)", label: `${statusCounts.completed} Done` });
            if (statusCounts.failed) items.push({ key: "fail", color: "var(--dan)", label: `${statusCounts.failed} Failed` });
            if (statusCounts.queued) items.push({ key: "queue", color: "var(--t3)", label: `${statusCounts.queued} Queued` });
            if (statusCounts.cancelled) items.push({ key: "cancel", color: "var(--t3)", label: `${statusCounts.cancelled} Cancelled` });
            return items.map((item) => (
              <div key={item.key} className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full" style={{ background: item.color }} />
                <span style={{ color: item.color }}>{item.label}</span>
              </div>
            ));
          })()}
        </div>
      )}

      {/* Loading skeleton */}
      {runsLoading ? (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="px-4 py-3 border-b border-border last:border-b-0">
              <Skeleton className="h-10 w-full rounded" />
            </div>
          ))}
        </div>
      ) : runs.length === 0 ? (
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <EmptyState
            variant="first-use"
            icon={<span className="text-muted-foreground">⧖</span>}
            title="还没有回测记录"
            description="创建你的第一个回测，在历史数据上验证策略表现"
            action={{ label: "+ 创建回测", onClick: onGoCreate }}
            hint="支持 Bar 和 Tick 粒度"
          />
        </div>
      ) : (
        <>
          {/* ZONE 1: Active tasks */}
          {activeRuns.length > 0 && (
            <div className="bg-card border border-border rounded-lg overflow-hidden">
              {activeRuns.map((run) => (
                <BacktestRunRow
                  key={run.run_id}
                  run={run}
                  progress={progressMap[run.run_id] ?? run.progress_pct ?? null}
                  progressDetail={progressDetailMap[run.run_id]}
                  expandedId={expandedId}
                  onToggleExpand={onToggleExpand}
                  onViewDetail={onViewDetail}
                />
              ))}
            </div>
          )}

          {/* ZONE 2: History */}
          {historyRuns.length > 0 && (
            <div className="mt-6">
              <div className="flex justify-between items-center mb-2.5">
                <div className="qds-section-label !mb-0">历史记录</div>
                <select
                  className="font-mono text-[0.68rem] px-2 py-1 bg-input border border-border rounded text-foreground outline-none cursor-pointer"
                  value={pageSize}
                  onChange={(e) => onPageSizeChange(Number(e.target.value))}
                >
                  <option value={10}>10 条/页</option>
                  <option value={20}>20 条/页</option>
                  <option value={50}>50 条/页</option>
                </select>
              </div>
              <div className="bg-card border border-border rounded-lg overflow-hidden">
                {historySlice.map((run) => (
                  <BacktestHistoryRow
                    key={run.run_id}
                    run={run}
                    expanded={expandedId === run.run_id}
                    onToggleExpand={onToggleExpand}
                    onViewDetail={onViewDetail}
                  />
                ))}
              </div>
              {totalHistoryPages > 1 && (
                <BacktestPagination
                  curPage={safePage}
                  totalPages={totalHistoryPages}
                  total={historyRuns.length}
                  pageSize={pageSize}
                  onPageChange={onPageChange}
                />
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
