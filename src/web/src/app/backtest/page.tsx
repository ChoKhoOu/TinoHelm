"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { apiPost } from "@/lib/api";
import { BacktestListView, type BacktestRunSummary } from "./components/BacktestListView";
import { BacktestDetailView } from "./components/BacktestDetailView";
import { BacktestTradesView } from "./components/BacktestTradesView";
import { BacktestCreateSheet } from "./components/BacktestCreateSheet";
import { useBacktestRuns } from "./hooks/useBacktestRuns";
import { useBacktestDetail } from "./hooks/useBacktestDetail";
import { useWsConnection, useWsEvent } from "@/providers/WebSocketProvider";

/* ------------------------------------------------------------------ */
/*  Main Page — route assembly + view switching                        */
/* ------------------------------------------------------------------ */

type View = "list" | "detail" | "trades";

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

  // Sheet state (replaces create view).
  const [sheetOpen, setSheetOpen] = useState(false);
  const [retryPrefill, setRetryPrefill] = useState<BacktestRunSummary | null>(null);

  // Detail tab data (trade log).
  const { tradeLog } = useBacktestDetail(selectedRunId, runs);

  const contentRef = useRef<HTMLDivElement>(null);

  // --- FR-013: WS stale detection ---
  const { connected: wsConnected } = useWsConnection();
  const [progressTimestamps, setProgressTimestamps] = useState<Record<string, number>>({});
  const [now, setNow] = useState(0);

  // Subscribe to backtest progress events to record last-seen timestamps.
  const progressMsg = useWsEvent("backtest.progress");
  useEffect(() => {
    if (!progressMsg) return;
    const raw = (progressMsg.data ?? progressMsg) as Record<string, unknown>;
    const rid = raw.run_id as string | undefined;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: record WS event timestamp to detect stale
    if (rid) setProgressTimestamps((prev) => ({ ...prev, [rid]: Date.now() }));
  }, [progressMsg]);

  // Tick every 3s to trigger stale re-evaluation. Init on client to avoid SSR hydration drift.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: interval-driven tick for stale re-eval
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 3000);
    return () => clearInterval(id);
  }, []);

  const isWsStale = useMemo(() => {
    if (!wsConnected) return true;
    const runningIds = runs.filter((r) => r.status === "running").map((r) => r.run_id);
    if (runningIds.length === 0) return false;
    return runningIds.some((rid) => {
      const ts = progressTimestamps[rid];
      return !ts || now - ts > 15000;
    });
  }, [wsConnected, runs, progressTimestamps, now]);

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

  // Sheet open handler — replaces old handleGoCreate.
  const handleGoCreate = () => {
    setRetryPrefill(null);
    setSheetOpen(true);
  };

  // Retry handler: prefills sheet from a previous run (FR-033).
  const handleRetry = (run: BacktestRunSummary) => {
    setRetryPrefill(run);
    setSheetOpen(true);
  };

  // Cancel a running backtest.
  const handleCancelRun = (runId: string) => {
    apiPost(`/api/backtest/${runId}/cancel`, {})
      .then(() => loadRuns())
      .catch(() => {});
  };

  // Navigate to trades view for a specific run.
  const handleViewAllTrades = (runId: string) => {
    setSelectedRunId(runId);
    setView("trades");
  };

  // Sheet submit: refresh runs list.
  const handleCreateSubmit = () => {
    loadRuns();
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
      {/* Create backtest sheet — rendered at top level, always available */}
      <BacktestCreateSheet
        key={sheetOpen ? (retryPrefill?.run_id ?? "new") : "closed"}
        open={sheetOpen}
        onOpenChange={setSheetOpen}
        retryPrefill={retryPrefill}
        onSubmit={handleCreateSubmit}
      />

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
            onRetryRun={handleRetry}
            onCancelRun={handleCancelRun}
            isWsStale={isWsStale}
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
            onViewAllTrades={handleViewAllTrades}
          />
        )}

        {/* ===== TRADES VIEW ===== */}
        {view === "trades" && selectedRun && (
          <BacktestTradesView
            selectedRun={selectedRun}
            tradeLog={tradeLog}
            onBack={() => setView("detail")}
          />
        )}
      </div>
    </div>
  );
}
