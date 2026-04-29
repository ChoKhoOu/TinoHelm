"use client";

import { useAction } from "@/hooks/use-action";
import { apiPost } from "@/lib/api";

/**
 * Cancel a factor run by run_id.
 *
 * POSTs to ``/api/factor/cancel/{runId}`` which sets the Redis cancel flag.
 * The factor worker re-checks the flag between every progress checkpoint and
 * transitions the run to ``cancelled`` within seconds.
 *
 * Layer-2 compliant: errors surface via ``action.error`` / ``<InlineError>``
 * at the call site — never via toast.
 */
export function useCancelFactorRun() {
  return useAction(
    async (runId: string) => {
      await apiPost<{ run_id: string; status: string }>(
        `/api/factor/cancel/${runId}`,
        {},
      );
      return { runId };
    },
    { successDuration: 2000 },
  );
}
