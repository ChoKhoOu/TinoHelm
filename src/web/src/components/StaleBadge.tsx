"use client";

import { cn } from "@/lib/utils";

interface StaleBadgeProps {
  status: "live" | "stale" | "disconnected";
  className?: string;
}

const config = {
  live: {
    dot: "bg-qds-success",
    text: "Live",
    textClass: "text-qds-success",
    wrapperClass: "",
  },
  stale: {
    dot: "bg-qds-warning",
    text: "Stale",
    textClass: "text-qds-t3 italic",
    wrapperClass: "",
  },
  disconnected: {
    dot: "bg-destructive",
    text: "Disconnected",
    textClass: "text-destructive",
    wrapperClass: "opacity-50",
  },
} as const;

export function StaleBadge({ status, className }: StaleBadgeProps) {
  const c = config[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-mono text-[0.68rem]",
        c.wrapperClass,
        className,
      )}
    >
      <span className={cn("inline-block size-1.5 rounded-full", c.dot)} />
      <span className={c.textClass}>{c.text}</span>
    </span>
  );
}
