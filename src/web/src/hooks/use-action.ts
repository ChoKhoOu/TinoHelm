import { useState, useCallback } from "react";

export type ActionState = "idle" | "loading" | "success" | "error";

interface UseActionResult<T> {
  state: ActionState;
  error: string | null;
  execute: (...args: any[]) => Promise<T | null>;
  reset: () => void;
}

export function useAction<T = unknown>(
  apiFn: (...args: any[]) => Promise<T>,
  options?: {
    successDuration?: number;  // ms before returning to idle (default: 1500)
    onSuccess?: (result: T) => void;
  }
): UseActionResult<T> {
  const [state, setState] = useState<ActionState>("idle");
  const [error, setError] = useState<string | null>(null);
  const successDuration = options?.successDuration ?? 1500;

  const execute = useCallback(async (...args: any[]): Promise<T | null> => {
    setState("loading");
    setError(null);
    try {
      const result = await apiFn(...args);
      setState("success");
      options?.onSuccess?.(result);
      setTimeout(() => setState("idle"), successDuration);
      return result;
    } catch (e: any) {
      setState("error");
      setError(e?.message || "操作失败");
      // Don't auto-recover — wait for user to retry
      return null;
    }
  }, [apiFn, successDuration, options?.onSuccess]);

  const reset = useCallback(() => {
    setState("idle");
    setError(null);
  }, []);

  return { state, error, execute, reset };
}
