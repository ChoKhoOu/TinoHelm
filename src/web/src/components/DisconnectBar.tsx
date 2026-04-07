"use client";

import { cn } from "@/lib/utils";

interface DisconnectBarProps {
  onRetry: () => void;
  className?: string;
}

export function DisconnectBar({ onRetry, className }: DisconnectBarProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-sm border border-destructive bg-qds-danger-dim px-3.5 py-2 font-mono text-[0.72rem] text-destructive",
        className,
      )}
    >
      <span className="relative flex size-2">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-destructive opacity-75" />
        <span className="relative inline-flex size-2 rounded-full bg-destructive" />
      </span>

      <span className="flex-1">连接已断开</span>

      <button
        onClick={onRetry}
        className="rounded-sm border border-destructive px-2.5 py-1 text-[0.68rem] font-medium text-destructive transition-colors duration-[var(--dur-fast)] hover:bg-destructive/10"
      >
        重新连接
      </button>
    </div>
  );
}
