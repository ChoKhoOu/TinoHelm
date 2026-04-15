import { useState, useCallback, useRef, useEffect } from "react";

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
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const execute = useCallback(async (...args: any[]): Promise<T | null> => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setState("loading");
    setError(null);
    try {
      const result = await apiFn(...args);
      setState("success");
      options?.onSuccess?.(result);
      timerRef.current = setTimeout(() => setState("idle"), successDuration);
      return result;
    } catch (e: any) {
      setState("error");
      setError(e?.message || "操作失败");
      return null;
    }
  }, [apiFn, successDuration, options?.onSuccess]);

  const reset = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setState("idle");
    setError(null);
  }, []);

  return { state, error, execute, reset };
}
