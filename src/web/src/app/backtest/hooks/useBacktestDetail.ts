"use client";

import { useEffect, useRef, useState } from "react";
import { apiGet } from "@/lib/api";
import type { BacktestResult, TradeLogEntry } from "../types";
import type { BacktestRunSummary } from "../components/BacktestListView";

/**
 * Detail-view state hook — trade log fetch + in-memory result cache.
 *
 * Only fetches when a selected run is completed; caches by run_id so that
 * flipping between tabs or detail/list views does not refetch.
 */
export function useBacktestDetail(
  selectedRunId: string | null,
  runs: BacktestRunSummary[],
) {
  const [tradeLog, setTradeLog] = useState<TradeLogEntry[]>([]);
  const resultCacheRef = useRef<Record<string, BacktestResult>>({});

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

  return { tradeLog };
}
