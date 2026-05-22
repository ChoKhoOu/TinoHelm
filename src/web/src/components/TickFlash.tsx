"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface TickFlashProps {
  value: number;
  children: ReactNode;
  className?: string;
}

export function TickFlash({ value, children, className }: TickFlashProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const prevValue = useRef(value);

  useEffect(() => {
    if (prevValue.current === value) return;
    const el = ref.current;
    if (!el) return;

    const cls = value > prevValue.current ? "flash-positive" : "flash-negative";
    el.classList.add(cls);

    const timer = setTimeout(() => {
      el.classList.remove(cls);
    }, 600);

    prevValue.current = value;
    return () => clearTimeout(timer);
  }, [value]);

  return (
    <span ref={ref} className={cn("transition-colors", className)}>
      {children}
    </span>
  );
}
