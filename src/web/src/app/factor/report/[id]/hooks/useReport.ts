"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import type { FactorReportResponse } from "../components/types";

interface UseReportState {
  report: FactorReportResponse | null;
  loading: boolean;
  error: string | null;
}

/**
 * Fetch the factor diagnostic report for a given ``run_id``.
 *
 * Endpoint: ``GET /api/factor/report/{run_id}``
 *
 * Returns:
 *   - ``report`` — the raw API response (status/result/error)
 *   - ``loading`` — fetch in-flight flag
 *   - ``error``   — network/transport error string (API 4xx/5xx bubble up)
 *   - ``reload()`` — bump the reload key to re-fetch
 *
 * Layer-2 contract: API errors surface through ``<InlineError />`` in the
 * caller; no toast for request-level failures.
 */
export function useReport(runId: string): UseReportState & {
  reload: () => void;
} {
  const [state, setState] = useState<UseReportState>({
    report: null,
    loading: !!runId,
    error: null,
  });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!runId) return;

    let cancelled = false;

    apiGet<FactorReportResponse>(`/api/factor/report/${runId}`)
      .then((data) => {
        if (cancelled) return;
        if (data) {
          setState({ report: data, loading: false, error: null });
        } else {
          setState({ report: null, loading: false, error: "报告不存在" });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setState({ report: null, loading: false, error: msg });
      });

    return () => {
      cancelled = true;
    };
  }, [runId, reloadKey]);

  const reload = () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    setReloadKey((k) => k + 1);
  };

  return { ...state, reload };
}
