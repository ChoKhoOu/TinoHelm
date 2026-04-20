"use client";

import { useEffect, useRef, useState } from "react";
import { apiGet } from "@/lib/api";
import { BacktestListView } from "./components/BacktestListView";
import { BacktestCreateView, type BacktestStrategyInfo } from "./components/BacktestCreateView";
import { BacktestDetailView } from "./components/BacktestDetailView";
import { useBacktestRuns } from "./hooks/useBacktestRuns";
import { useBacktestDetail } from "./hooks/useBacktestDetail";

/* ------------------------------------------------------------------ */
/*  Main Page — route assembly + view switching                        */
/* ------------------------------------------------------------------ */

type View = "list" | "create" | "detail";

export default function BacktestPage() {
  // Data hooks (runs list + WS progress + polling; detail trade log cache).
  const { runs, runsLoading, progressMap, progressDetailMap, loadRuns } = useBacktestRuns();

  // View + selection state.
  const [view, setView] = useState<View>("list");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("overview");
  const [curPage, setCurPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // Strategies list for the create form.
  const [strategies, setStrategies] = useState<BacktestStrategyInfo[]>([]);

  // Detail tab data (trade log).
  const { tradeLog } = useBacktestDetail(selectedRunId, runs);

  const contentRef = useRef<HTMLDivElement>(null);

  // Load strategies once.
  useEffect(() => {
    apiGet<BacktestStrategyInfo[]>("/api/strategies")
      .then((d) => d && setStrategies(d))
      .catch(() => {});
  }, []);

  // View handlers.
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

  // Live progress for the detail pane — falls back to server-persisted pct.
  const detailProgressPct = selectedRunId
    ? progressMap[selectedRunId] ?? selectedRun?.progress_pct ?? 0
    : 0;
  const detailProgressMessage = selectedRunId
    ? progressDetailMap[selectedRunId]?.message
    : undefined;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-1 overflow-y-auto px-8 pb-16" ref={contentRef}>

        {/* ===== LIST VIEW ===== */}
        {view === "list" && (
          <BacktestListView
            runs={runs}
            runsLoading={runsLoading}
            progressMap={progressMap}
            progressDetailMap={progressDetailMap}
            expandedId={expandedId}
            curPage={curPage}
            pageSize={pageSize}
            onRefresh={loadRuns}
            onGoCreate={handleGoCreate}
            onToggleExpand={handleToggleExpand}
            onViewDetail={handleViewDetail}
            onPageChange={setCurPage}
            onPageSizeChange={(size) => { setPageSize(size); setCurPage(1); }}
          />
        )}

        {/* ===== CREATE VIEW ===== */}
        {view === "create" && (
          <BacktestCreateView
            strategies={strategies}
            onSubmit={async () => { await loadRuns(); }}
            onCancel={handleBackFromCreate}
          />
        )}

        {/* ===== DETAIL VIEW ===== */}
        {view === "detail" && selectedRun && selectedRunId && (
          <BacktestDetailView
            selectedRun={selectedRun}
            selectedRunId={selectedRunId}
            activeTab={activeTab}
            setActiveTab={setActiveTab}
            progressPct={detailProgressPct}
            progressMessage={detailProgressMessage}
            tradeLog={tradeLog}
            onBack={handleBack}
          />
        )}
      </div>
    </div>
  );
}
