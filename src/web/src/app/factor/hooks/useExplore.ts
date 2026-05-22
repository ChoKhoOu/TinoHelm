"use client";

import { useState } from "react";
import { apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import type { ExploreRequest, ExploreResult } from "../components/types";

/**
 * Wrap the POST /api/factor/explore call in a ``useAction`` state machine.
 *
 * - Exposes ``state`` (idle|loading|success|error) for inline button feedback
 * - Stashes the most recent ``result`` for the right-hand panel
 * - ``useAction`` already handles API errors into ``action.error`` — the
 *   caller renders them with ``<InlineError />`` (Layer-2 contract, NO toast)
 */
export function useExplore() {
  const [result, setResult] = useState<ExploreResult | null>(null);
  const [lastFactorName, setLastFactorName] = useState<string | null>(null);

  const action = useAction(
    async (payload: ExploreRequest) =>
      (await apiPost<ExploreResult>("/api/factor/explore", payload)) as ExploreResult,
    {
      onSuccess: (res) => {
        if (res) {
          setResult(res);
          setLastFactorName(res.factor_name);
        }
      },
    },
  );

  return {
    state: action.state,
    error: action.error,
    result,
    lastFactorName,
    hasRun: result !== null,
    execute: action.execute,
    reset: () => {
      setResult(null);
      setLastFactorName(null);
      action.reset();
    },
  };
}
