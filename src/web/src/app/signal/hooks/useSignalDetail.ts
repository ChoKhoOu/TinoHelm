"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import type { SignalReportResponse } from "../types";

interface UseSignalDetailResult {
  report: SignalReportResponse | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Load a single SignalRun report (GET /api/signal/report/{run_id}).
 *
 * Polls every ``pollMs`` (default 4 s) when the run is in flight (queued /
 * running) so the detail page surfaces progress + final result without
 * requiring a manual refresh.  Polling stops once the status reaches a
 * terminal state (completed / failed / cancelled).
 */
export function useSignalDetail(
  runId: string | null,
  { pollMs = 4_000 }: { pollMs?: number } = {},
): UseSignalDetailResult {
  const [report, setReport] = useState<SignalReportResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!runId) {
      setReport(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const fetchOnce = async () => {
      try {
        const res = await apiGet<SignalReportResponse>(
          `/api/signal/report/${runId}`,
        );
        if (cancelled) return;
        setReport(res ?? null);
        setError(null);
        return res;
      } catch (e) {
        if (cancelled) return null;
        setError(e instanceof Error ? e.message : String(e));
        return null;
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    setLoading(true);

    const tick = async () => {
      if (cancelled) return;
      const res = await fetchOnce();
      if (cancelled) return;
      const terminal =
        res?.status === "completed" ||
        res?.status === "failed" ||
        res?.status === "cancelled";
      if (!terminal && pollMs > 0) {
        timer = setTimeout(tick, pollMs);
      }
    };

    tick();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, pollMs, reloadKey]);

  return {
    report,
    loading,
    error,
    reload: () => setReloadKey((k) => k + 1),
  };
}
