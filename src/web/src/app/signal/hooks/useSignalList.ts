"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import type {
  SignalListItem,
  SignalRunsPage,
  SignalRunStatusFilter,
} from "../types";

interface UseSignalListResult {
  items: SignalListItem[];
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Load the registry of available signals (GET /api/signal/list).
 *
 * Returns ``include_deprecated=false`` view by default — matches the backend
 * default behaviour.
 */
export function useSignalCatalogue(): UseSignalListResult {
  const [items, setItems] = useState<SignalListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await apiGet<SignalListItem[]>("/api/signal/list");
        if (cancelled) return;
        setItems(res ?? []);
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

  return {
    items,
    loading,
    error,
    reload: () => setReloadKey((k) => k + 1),
  };
}

/* ------------------------------------------------------------------ */
/*  useSignalRuns — paginated runs list with status filter             */
/* ------------------------------------------------------------------ */

interface UseSignalRunsArgs {
  page: number;
  pageSize: number;
  status: SignalRunStatusFilter;
  /** Polling interval (ms).  ``0`` disables polling.  Default 5_000. */
  pollMs?: number;
}

interface UseSignalRunsResult extends SignalRunsPage {
  loading: boolean;
  error: string | null;
  reload: () => void;
}

/**
 * Load /api/signal/runs with pagination + status filter.
 *
 * Polls every ``pollMs`` (default 5 s) so running rows surface progress
 * updates without requiring a WebSocket subscription.  The hook follows the
 * existing backtest-page polling pattern (see ``useBacktestRuns``).
 */
export function useSignalRuns({
  page,
  pageSize,
  status,
  pollMs = 5_000,
}: UseSignalRunsArgs): UseSignalRunsResult {
  const [data, setData] = useState<SignalRunsPage>({
    runs: [],
    page,
    page_size: pageSize,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const fetchOnce = async () => {
      try {
        const params: Record<string, string> = {
          page: String(page),
          page_size: String(pageSize),
        };
        if (status !== "all") params.status = status;

        const res = await apiGet<SignalRunsPage>("/api/signal/runs", params);
        if (cancelled) return;
        setData(
          res ?? { runs: [], page, page_size: pageSize },
        );
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    setLoading(true);
    fetchOnce();

    if (pollMs > 0) {
      const tick = () => {
        if (cancelled) return;
        fetchOnce().finally(() => {
          if (!cancelled && pollMs > 0) {
            timer = setTimeout(tick, pollMs);
          }
        });
      };
      timer = setTimeout(tick, pollMs);
    }

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [page, pageSize, status, pollMs, reloadKey]);

  return {
    runs: data.runs,
    page: data.page,
    page_size: data.page_size,
    loading,
    error,
    reload: () => setReloadKey((k) => k + 1),
  };
}
