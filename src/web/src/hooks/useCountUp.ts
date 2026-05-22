"use client";

import { useState, useEffect, useRef } from "react";

export function useCountUp(target: number, duration = 800, enabled = true): number {
  const [value, setValue] = useState(target);
  const rafRef = useRef<number | null>(null);
  const prevTargetRef = useRef(target);

  useEffect(() => {
    if (!enabled) {
      setValue(target);
      prevTargetRef.current = target;
      return;
    }
    const from = prevTargetRef.current;
    prevTargetRef.current = target;

    if (from === target) {
      setValue(target);
      return;
    }

    const start = performance.now();

    function tick(now: number) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // cubic easing
      setValue(from + (target - from) * eased);
      if (progress < 1) {
        rafRef.current = requestAnimationFrame(tick);
      } else {
        setValue(target);
      }
    }

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration, enabled]);

  return value;
}
