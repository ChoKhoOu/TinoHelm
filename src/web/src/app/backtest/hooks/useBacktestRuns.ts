"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import type { BacktestProgressDetail, BacktestRunSummary } from "../components/BacktestListView";

/**
 * Runs list + WebSocket progress + 5s polling hook.
 *
 * Encapsulates the list-view data flow previously inline in page.tsx. Returns
 * the current run list plus the live progress maps so that parent route can
 * pass them straight into BacktestListView / BacktestDetailView.
 */
export function useBacktestRuns() {
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});
  const [progressDetailMap, setProgressDetailMap] = useState<Record<string, BacktestProgressDetail>>({});

  const wsMsg = useWsEvent("backtest.progress");

  // Load runs list (re-usable by form submit / refresh button).
  const loadRuns = useCallback(async () => {
    try {
      const data = await apiGet<{ runs: BacktestRunSummary[]; total: number }>(
        "/api/backtest/runs?limit=100",
      );
      if (data) setRuns(data.runs ?? []);
    } catch {
      // ignore
    } finally {
      setRunsLoading(false);
    }
  }, []);

  // Polling every 5s + initial load.
  useEffect(() => {
    loadRuns();
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, [loadRuns]);

  // Apply WS progress updates. When a run emits progress events while the
  // list still shows it as queued, optimistically flip status to running.
  useEffect(() => {
    if (!wsMsg) return;
    const raw = (wsMsg.data ?? wsMsg) as Record<string, unknown>;
    const run_id = raw.run_id as string;
    const pct = raw.pct as number;
    if (!run_id) return;

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
          : r,
      ),
    );
  }, [wsMsg]);

  return {
    runs,
    runsLoading,
    progressMap,
    progressDetailMap,
    loadRuns,
  };
}
