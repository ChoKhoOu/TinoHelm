"use client";

import { useCallback, useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import type { BacktestProgressDetail, BacktestRunSummary } from "../components/BacktestListView";

// Terminal statuses — runs in these states must NEVER be re-upgraded
// to running by a stale WS event, and their progress entries can be
// safely swept after polling confirms the terminal state. Module-scoped
// so it's a stable reference across renders (no deps-array churn).
const TERMINAL_STATUSES: ReadonlyArray<BacktestRunSummary["status"]> = [
  "completed",
  "failed",
  "cancelled",
];

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
  // After fetching, sweep terminal runs out of the progress maps so
  // stale WS progress doesn't continue to render "Running 7%" on a
  // completed row.
  const loadRuns = useCallback(async () => {
    try {
      const data = await apiGet<{ runs: BacktestRunSummary[]; total: number }>(
        "/api/backtest/runs?limit=100",
      );
      if (data) {
        const next = data.runs ?? [];
        setRuns(next);

        const terminalIds = new Set(
          next
            .filter((r) => TERMINAL_STATUSES.includes(r.status))
            .map((r) => r.run_id),
        );
        if (terminalIds.size > 0) {
          setProgressMap((prev) => {
            let changed = false;
            const out: Record<string, number> = {};
            for (const [k, v] of Object.entries(prev)) {
              if (terminalIds.has(k)) {
                changed = true;
                continue;
              }
              out[k] = v;
            }
            return changed ? out : prev;
          });
          setProgressDetailMap((prev) => {
            let changed = false;
            const out: Record<string, BacktestProgressDetail> = {};
            for (const [k, v] of Object.entries(prev)) {
              if (terminalIds.has(k)) {
                changed = true;
                continue;
              }
              out[k] = v;
            }
            return changed ? out : prev;
          });
        }
      }
    } catch {
      // ignore
    } finally {
      setRunsLoading(false);
    }
  }, []);

  // Polling every 5s + initial load. Also listen for `tino:ws-visible`
  // (dispatched from useWebSocket on tab visibility resume) to refresh
  // immediately rather than waiting for the next 5s tick.
  useEffect(() => {
    loadRuns();
    const interval = setInterval(loadRuns, 5000);
    const onVisible = () => {
      loadRuns();
    };
    if (typeof window !== "undefined") {
      window.addEventListener("tino:ws-visible", onVisible);
    }
    return () => {
      clearInterval(interval);
      if (typeof window !== "undefined") {
        window.removeEventListener("tino:ws-visible", onVisible);
      }
    };
  }, [loadRuns]);

  // Apply WS progress updates. Optimistically flip status to running
  // ONLY when the run is currently queued — never when it is already
  // in a terminal state (completed / failed / cancelled). This guards
  // against out-of-order WS frames arriving after polling has already
  // seen the terminal status.
  useEffect(() => {
    if (!wsMsg) return;
    const raw = (wsMsg.data ?? wsMsg) as Record<string, unknown>;
    const run_id = raw.run_id as string;
    const pct = raw.pct as number;
    if (!run_id) return;

    setRuns((prev) => {
      const target = prev.find((r) => r.run_id === run_id);
      // If we already know the run ended, drop the event entirely —
      // don't pollute progressMap with stale pct for a completed row.
      if (target && TERMINAL_STATUSES.includes(target.status)) {
        return prev;
      }

      setProgressMap((m) => ({ ...m, [run_id]: pct }));
      setProgressDetailMap((m) => ({
        ...m,
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

      return prev.map((r) =>
        r.run_id === run_id && r.status === "queued"
          ? { ...r, status: "running" }
          : r,
      );
    });
  }, [wsMsg]);

  return {
    runs,
    runsLoading,
    progressMap,
    progressDetailMap,
    loadRuns,
  };
}
