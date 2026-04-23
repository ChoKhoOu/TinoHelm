"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import type { FactorSpec } from "../components/types";

interface FactorListState {
  factors: FactorSpec[];
  universes: string[];
  symbols: string[];
  loading: boolean;
  error: string | null;
}

/**
 * Load the three factor-metadata endpoints in parallel.
 *
 * Endpoints:
 *   - GET /api/factor/list       → FactorSpec[]
 *   - GET /api/factor/universes  → string[]
 *   - GET /api/factor/symbols    → string[]
 *
 * Returns empty arrays on failure instead of throwing; the page surface
 * displays an <InlineError /> when ``error`` is set.
 */
export function useFactorList(): FactorListState & { reload: () => void } {
  const [state, setState] = useState<FactorListState>({
    factors: [],
    universes: [],
    symbols: [],
    loading: true,
    error: null,
  });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const [factors, universes, symbols] = await Promise.all([
          apiGet<FactorSpec[]>("/api/factor/list").catch(() => []),
          apiGet<string[]>("/api/factor/universes").catch(() => []),
          apiGet<string[]>("/api/factor/symbols").catch(() => []),
        ]);
        if (cancelled) return;
        setState({
          factors: factors ?? [],
          universes: universes ?? [],
          symbols: symbols ?? [],
          loading: false,
          error: null,
        });
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof Error ? e.message : String(e);
        setState({
          factors: [],
          universes: [],
          symbols: [],
          loading: false,
          error: msg,
        });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  return { ...state, reload: () => setReloadKey((k) => k + 1) };
}
