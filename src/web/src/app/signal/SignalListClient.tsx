"use client";

import { useMemo, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import {
  HelpTip,
  InlineError,
  PageHeader,
  SectionLabel,
} from "@/components/qds";
import { useSignalCatalogue, useSignalRuns } from "./hooks/useSignalList";
import { SignalRow } from "./components/SignalRow";
import {
  STATUS_FILTER_OPTIONS,
  type SignalRunStatusFilter,
} from "./types";

const PAGE_SIZE = 20;

/**
 * Top-level client component for the /signal route.
 *
 * Layout (full-width single column to match the backtest list page):
 *
 *   ┌─────────────────────────────────────────────┐
 *   │ PageHeader (Signals · 信号库)                │
 *   ├─────────────────────────────────────────────┤
 *   │ Filter tabs (all / running / queued / done) │
 *   ├─────────────────────────────────────────────┤
 *   │ SignalRow list (paginated)                  │
 *   └─────────────────────────────────────────────┘
 *
 * Polling: ``useSignalRuns`` polls every 5 s so running rows surface
 * progress updates without WS plumbing — matches the backtest hooks.
 */
export function SignalListClient() {
  const [statusFilter, setStatusFilter] = useState<SignalRunStatusFilter>("all");
  const [page, setPage] = useState(1);

  const { items: catalogue, error: catalogueError } = useSignalCatalogue();
  const { runs, loading, error, reload } = useSignalRuns({
    page,
    pageSize: PAGE_SIZE,
    status: statusFilter,
  });

  /* Per-status counts on the *current page* — for the filter chips. */
  const counts = useMemo(() => {
    const out: Record<string, number> = { all: runs.length };
    runs.forEach((r) => {
      out[r.status] = (out[r.status] ?? 0) + 1;
    });
    return out;
  }, [runs]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="px-6 py-3.5 border-b flex-shrink-0">
        <PageHeader
          title="Signals"
          subtitle={`信号库 · ${catalogue.length} 个已注册 · 历史 run 状态轮询`}
          actions={
            <Button variant="outline" size="sm" onClick={reload} disabled={loading}>
              <RotateCcw className="w-3.5 h-3.5" />
              刷新
            </Button>
          }
        />
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 pb-16">
        {/* Filter tabs (segmented pill, mirrors component-tabs.html .tabs) */}
        <div className="flex items-center gap-2 mb-4">
          <SectionLabel>Runs · 信号运行</SectionLabel>
          <HelpTip text="status filter: all 显示全部；其它过滤为后端返回值。每 5s 自动刷新。" />
          <div className="ml-auto inline-flex bg-input rounded-md p-[3px] gap-[2px]">
            {STATUS_FILTER_OPTIONS.map((opt) => {
              const active = opt.id === statusFilter;
              const count = opt.id === "all" ? runs.length : counts[opt.id] ?? 0;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => {
                    setStatusFilter(opt.id);
                    setPage(1);
                  }}
                  className={`font-mono text-[0.66rem] px-3 py-1.5 rounded-sm cursor-pointer transition-colors inline-flex items-center gap-1.5 ${
                    active
                      ? "bg-secondary text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {opt.label}
                  {opt.id !== "all" && count > 0 && (
                    <span
                      className={`text-[0.55rem] px-1.5 py-px rounded-full ${
                        active
                          ? "bg-qds-accent-dim text-primary"
                          : "bg-secondary text-muted-foreground"
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>

        {/* Catalog summary band — show first 5 registered signal names */}
        {catalogue.length > 0 && (
          <div className="rounded-lg border bg-card px-4 py-3 mb-4 flex items-center gap-3 flex-wrap">
            <span className="font-mono text-[0.6rem] uppercase tracking-widest text-primary">
              Catalogue
            </span>
            <div className="flex items-center gap-1.5 flex-wrap">
              {catalogue.slice(0, 6).map((s) => (
                <span
                  key={s.name}
                  className="font-mono text-[0.65rem] px-2 py-0.5 rounded-full bg-input text-foreground"
                  title={`${s.method} · factor=${s.factor_ref} · ${s.rebalance_freq}`}
                >
                  {s.name}
                </span>
              ))}
              {catalogue.length > 6 && (
                <span className="font-mono text-[0.65rem] text-muted-foreground">
                  +{catalogue.length - 6}
                </span>
              )}
            </div>
          </div>
        )}

        {/* List body */}
        {error ? (
          <InlineError>{`加载失败: ${error}`}</InlineError>
        ) : loading && runs.length === 0 ? (
          <div className="rounded-lg border bg-card p-6 text-center font-mono text-[0.7rem] text-muted-foreground">
            加载中...
          </div>
        ) : runs.length === 0 ? (
          <EmptyState
            variant="no-results"
            title={
              statusFilter === "all"
                ? "暂无 SignalRun 记录"
                : `暂无 ${statusFilter} 状态的 SignalRun`
            }
            description="使用 POST /api/signal/run 创建一个新的信号评估，或换个 status 过滤条件。"
          />
        ) : (
          <div className="rounded-lg border bg-card overflow-hidden">
            {runs.map((run) => (
              <SignalRow key={run.run_id} run={run} />
            ))}
          </div>
        )}

        {/* Pagination */}
        <div className="flex items-center justify-between mt-4">
          <span className="font-mono text-[0.65rem] text-muted-foreground">
            page {page} · {runs.length} 行
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={runs.length < PAGE_SIZE || loading}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>

        {catalogueError && (
          <InlineError>{`Catalogue 加载失败: ${catalogueError}`}</InlineError>
        )}
      </div>
    </div>
  );
}
