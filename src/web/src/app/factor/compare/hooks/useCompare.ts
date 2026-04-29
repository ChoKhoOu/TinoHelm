"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import type {
  CompareMultiRequest,
  CompareMultiResult,
  FactorRunSummary,
} from "../types";

/**
 * Load completed FactorRun records for the multi-select picker.
 *
 * Endpoint: ``GET /api/factor/runs?limit=200`` — returns up to 200 most-recent
 * records.  Filtering for ``status === "completed"`` happens client-side so
 * the user can see in-flight runs (greyed) but only select completed ones.
 */
export function useFactorRuns() {
  const [runs, setRuns] = useState<FactorRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiGet<FactorRunSummary[]>("/api/factor/runs", {
          limit: "200",
        });
        if (cancelled) return;
        setRuns(res ?? []);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return { runs, loading, error, reload: () => setReloadKey((k) => k + 1) };
}

/**
 * POST /api/factor/compare/multi — wraps the call in a ``useAction`` state
 * machine + caches the most recent result for the right-hand panel.
 *
 * Layer-2 contract: API errors surface in ``action.error`` and are rendered
 * with ``<InlineError />``; never via toast.
 */
export function useCompareMulti() {
  const [result, setResult] = useState<CompareMultiResult | null>(null);

  const action = useAction(
    async (payload: CompareMultiRequest) =>
      (await apiPost<CompareMultiResult>(
        "/api/factor/compare/multi",
        payload,
      )) as CompareMultiResult,
    {
      onSuccess: (res) => {
        if (res) setResult(res);
      },
    },
  );

  return {
    state: action.state,
    error: action.error,
    result,
    hasRun: result !== null,
    execute: action.execute,
    reset: () => {
      setResult(null);
      action.reset();
    },
  };
}
