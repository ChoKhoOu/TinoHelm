import { useState, useCallback, useRef, useEffect } from "react";

export type ActionState = "idle" | "loading" | "success" | "error";

interface UseActionResult<T, Args extends unknown[]> {
  state: ActionState;
  error: string | null;
  execute: (...args: Args) => Promise<T | null>;
  reset: () => void;
}

export function useAction<T = unknown, Args extends unknown[] = unknown[]>(
  apiFn: (...args: Args) => Promise<T>,
  options?: {
    successDuration?: number;  // ms before returning to idle (default: 1500)
    onSuccess?: (result: T) => void;
  }
): UseActionResult<T, Args> {
  const [state, setState] = useState<ActionState>("idle");
  const [error, setError] = useState<string | null>(null);
  const successDuration = options?.successDuration ?? 1500;
  const onSuccess = options?.onSuccess;
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const execute = useCallback(async (...args: Args): Promise<T | null> => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setState("loading");
    setError(null);
    try {
      const result = await apiFn(...args);
      setState("success");
      onSuccess?.(result);
      timerRef.current = setTimeout(() => setState("idle"), successDuration);
      return result;
    } catch (e: unknown) {
      setState("error");
      setError(e instanceof Error ? e.message : "操作失败");
      return null;
    }
  }, [apiFn, successDuration, onSuccess]);

  const reset = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setState("idle");
    setError(null);
  }, []);

  return { state, error, execute, reset };
}
