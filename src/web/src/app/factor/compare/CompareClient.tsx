"use client";

import { useEffect, useMemo, useState } from "react";
import { GitCompare, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import {
  HelpTip,
  InlineError,
  PageHeader,
  SectionLabel,
  StatusBadge,
} from "@/components/qds";
import { useCompareMulti, useFactorRuns } from "./hooks/useCompare";
import { RankingHeatmap } from "./components/RankingHeatmap";
import { RollingICSmallMultiples } from "./components/RollingICSmallMultiples";
import { Dendrogram } from "./components/Dendrogram";
import type { FactorRunSummary } from "./types";

type ReportTab = "ranking" | "rolling" | "dendrogram";

const TABS: { id: ReportTab; label: string; help: string }[] = [
  {
    id: "ranking",
    label: "Ranking",
    help: "Factor × Metric 1-based 排名表，颜色越深 = 排名越靠前。",
  },
  {
    id: "rolling",
    label: "Rolling IC",
    help: "每个因子单独 mini chart，30-bar rolling mean。",
  },
  {
    id: "dendrogram",
    label: "Dendrogram",
    help: "基于 IC 时间序列相关性的 Ward 层次聚类。",
  },
];

/**
 * /factor/compare client shell.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────┐
 *   │ PageHeader (Factor Compare · 多因子横向比较)  │
 *   ├────────────┬─────────────────────────────────┤
 *   │ Left:      │ Right:                           │
 *   │ FactorRun  │ TabNav (Ranking / Rolling /      │
 *   │ multi-     │   Dendrogram)                    │
 *   │ select     │ Report panel content             │
 *   │ + Run btn  │                                  │
 *   └────────────┴─────────────────────────────────┘
 *
 * Layer-2 contract: API errors render via ``<InlineError />`` next to the
 * action button — no toast.
 */
export function CompareClient() {
  const { runs, loading, error: runsError, reload } = useFactorRuns();
  const compare = useCompareMulti();

  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState<ReportTab>("ranking");

  /* Auto-clear selections that are no longer in the runs list (after reload). */
  useEffect(() => {
    if (runs.length === 0) return;
    setSelectedIds((prev) =>
      prev.filter((id) => runs.some((r) => r.run_id === id)),
    );
  }, [runs]);

  const completedRuns = useMemo(
    () => runs.filter((r) => r.status === "completed"),
    [runs],
  );

  const toggleRun = (runId: string) => {
    setSelectedIds((prev) =>
      prev.includes(runId)
        ? prev.filter((id) => id !== runId)
        : [...prev, runId],
    );
  };

  const canRun = selectedIds.length >= 2 && compare.state !== "loading";

  const runCompare = async () => {
    if (!canRun) return;
    await compare.execute({
      eval_run_ids: selectedIds,
      n_bootstrap: 1000,
    });
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-6 py-3.5 border-b flex-shrink-0">
        <PageHeader
          title="Factor Compare"
          subtitle="多因子横向比较 · ranking heatmap · rolling IC · dendrogram"
          actions={
            <Button
              variant="outline"
              size="sm"
              onClick={reload}
              disabled={loading}
            >
              <RotateCcw className="w-3.5 h-3.5" />
              刷新
            </Button>
          }
        />
      </div>

      {/* Two-pane body */}
      <div className="flex flex-1 overflow-hidden">
        {/* LEFT · Factor run picker */}
        <aside className="w-80 min-w-80 border-r overflow-y-auto bg-background p-4">
          <div className="flex items-center gap-2 mb-3">
            <SectionLabel>FactorRuns · 待对比</SectionLabel>
          </div>

          <div className="font-mono text-[0.65rem] text-muted-foreground mb-3 flex items-center gap-1">
            选择 ≥ 2 个 completed 因子运行
            <HelpTip text="后端 /api/factor/compare/multi 至少需要 2 个 eval_run_ids。运行中或失败的不可勾选。" />
          </div>

          {loading ? (
            <div className="font-mono text-[0.7rem] text-muted-foreground py-4 text-center">
              加载中...
            </div>
          ) : completedRuns.length === 0 ? (
            <EmptyState
              variant="no-results"
              size="section"
              title="暂无可对比的 FactorRun"
              description="请先在 /factor 页面执行至少 2 次因子探索（POST /api/factor/run），等待 completed 后再来此页对比。"
            />
          ) : (
            <ul className="flex flex-col gap-1.5">
              {completedRuns.map((run) => (
                <FactorRunPickerRow
                  key={run.run_id}
                  run={run}
                  selected={selectedIds.includes(run.run_id)}
                  onToggle={() => toggleRun(run.run_id)}
                />
              ))}
            </ul>
          )}

          {/* Action area */}
          <div className="flex flex-col gap-2 mt-5">
            <Button
              variant={compare.state === "error" ? "destructive" : "default"}
              onClick={runCompare}
              disabled={!canRun}
              data-testid="run-compare-multi"
            >
              {compare.state === "loading" ? (
                "对比中..."
              ) : compare.state === "success" ? (
                "✓ 已生成报告"
              ) : (
                <>
                  <GitCompare className="w-3.5 h-3.5" />
                  生成对比报告 ({selectedIds.length})
                </>
              )}
            </Button>
            {compare.state === "error" && compare.error && (
              <InlineError>{compare.error}</InlineError>
            )}
            {runsError && (
              <InlineError>{`FactorRuns 加载失败: ${runsError}`}</InlineError>
            )}
            {selectedIds.length === 1 && (
              <InlineError variant="hint">
                还需选择至少 1 个因子才能对比
              </InlineError>
            )}
          </div>
        </aside>

        {/* RIGHT · Report panel */}
        <main className="flex-1 overflow-y-auto px-7 py-5 pb-16">
          {!compare.hasRun ? (
            <EmptyState
              variant="first-use"
              title="选择因子开始对比"
              description="左侧勾选 ≥ 2 个 completed FactorRun，点击「生成对比报告」查看 ranking、rolling IC、dendrogram 三视图。"
            />
          ) : (
            compare.result && (
              <div className="flex flex-col gap-5">
                {/* Agent summary banner */}
                <div className="rounded-lg border bg-card p-4 flex items-start gap-3">
                  <div className="rounded-md bg-qds-accent-dim text-primary p-2 mt-0.5">
                    <GitCompare className="w-4 h-4" />
                  </div>
                  <div className="flex-1">
                    <div className="font-mono text-[0.6rem] uppercase tracking-widest text-primary mb-2">
                      Agent Summary
                    </div>
                    {/* Top performers */}
                    {compare.result.agent_summary.top_performers.length > 0 && (
                      <div className="mb-2">
                        <div className="font-mono text-[0.65rem] text-muted-foreground mb-1">
                          Top Performers
                        </div>
                        <ul className="flex flex-col gap-1">
                          {compare.result.agent_summary.top_performers.map((p, i) => (
                            <li key={p.name} className="flex items-baseline gap-2 text-sm">
                              <span className="font-mono text-[0.65rem] text-muted-foreground w-4 shrink-0">
                                #{i + 1}
                              </span>
                              <span className="font-mono text-[0.78rem] font-medium text-foreground">
                                {p.name}
                              </span>
                              <span className="text-[0.72rem] text-muted-foreground">
                                {p.why}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {/* Warnings */}
                    {compare.result.agent_summary.warnings.length > 0 && (
                      <div>
                        <div className="font-mono text-[0.65rem] text-muted-foreground mb-1">
                          Warnings
                        </div>
                        <ul className="flex flex-col gap-0.5">
                          {compare.result.agent_summary.warnings.map((w) => (
                            <li
                              key={`${w.factor}-${w.type}`}
                              className="text-[0.72rem] text-qds-warning"
                            >
                              {w.message}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {/* Empty state */}
                    {compare.result.agent_summary.top_performers.length === 0 && (
                      <div className="text-sm text-muted-foreground">
                        No valid factors to compare.
                      </div>
                    )}
                  </div>
                </div>

                {/* Tab nav (underline pattern from component-tabs.html) */}
                <div className="flex border-b">
                  {TABS.map((tab) => {
                    const isActive = tab.id === activeTab;
                    return (
                      <button
                        key={tab.id}
                        type="button"
                        onClick={() => setActiveTab(tab.id)}
                        className={`relative font-mono text-[0.78rem] px-4 py-2.5 cursor-pointer transition-colors hover:text-foreground inline-flex items-center gap-1.5 ${
                          isActive ? "text-primary" : "text-muted-foreground"
                        }`}
                      >
                        {tab.label}
                        <HelpTip text={tab.help} />
                        {isActive && (
                          <span className="absolute left-0 right-0 -bottom-px h-0.5 bg-primary rounded-full" />
                        )}
                      </button>
                    );
                  })}
                </div>

                {/* Tab body */}
                {activeTab === "ranking" && (
                  <RankingHeatmap heatmap={compare.result.ranking_heatmap} />
                )}
                {activeTab === "rolling" && (
                  <RollingICSmallMultiples
                    rolling={compare.result.rolling_ic_small_multiples}
                  />
                )}
                {activeTab === "dendrogram" && (
                  <Dendrogram data={compare.result.dendrogram} />
                )}
              </div>
            )
          )}
        </main>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  FactorRunPickerRow — single selectable row in left panel           */
/* ------------------------------------------------------------------ */

interface FactorRunPickerRowProps {
  run: FactorRunSummary;
  selected: boolean;
  onToggle: () => void;
}

function FactorRunPickerRow({ run, selected, onToggle }: FactorRunPickerRowProps) {
  const created = run.created_at ? run.created_at.slice(0, 16).replace("T", " ") : "—";
  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        className={`w-full flex items-start gap-2.5 px-3 py-2 rounded-md border text-left transition-colors hover:bg-secondary ${
          selected
            ? "border-primary bg-qds-accent-dim"
            : "border-border bg-card"
        }`}
      >
        <span
          className={`mt-1 inline-flex items-center justify-center w-3.5 h-3.5 rounded-sm border text-[0.6rem] flex-shrink-0 ${
            selected ? "border-primary bg-primary text-primary-foreground" : "border-border"
          }`}
          aria-hidden
        >
          {selected ? "✓" : ""}
        </span>
        <div className="flex-1 min-w-0">
          <div className="font-mono text-[0.78rem] font-medium text-foreground truncate">
            {run.factor_name}
          </div>
          <div className="font-mono text-[0.6rem] text-muted-foreground truncate">
            {run.run_id.slice(0, 8)} · {created}
          </div>
        </div>
        <StatusBadge status={run.status} locale="en" />
      </button>
    </li>
  );
}
