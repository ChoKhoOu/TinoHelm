"use client";

const STATUS_STYLES: Record<string, string> = {
  queued: "bg-[var(--accent-amber-20)] text-[var(--accent-amber)]",
  running: "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]",
  completed: "bg-[var(--accent-green-20)] text-[var(--accent-green)]",
  failed: "bg-[var(--accent-red-20)] text-[var(--accent-red)]",
  cancelling: "bg-[var(--bg-subtle)] text-[var(--text-muted)]",
  cancelled: "bg-[var(--bg-subtle)] text-[var(--text-muted)]",
};

const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelling: "取消中",
  cancelled: "已取消",
};

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className = "" }: StatusBadgeProps) {
  const key = status.toLowerCase();
  const style = STATUS_STYLES[key] ?? "bg-[var(--bg-subtle)] text-[var(--text-muted)]";
  const label = STATUS_LABELS[key] ?? status;
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded-full text-[9px] font-bold ${style} ${className}`}>
      {label}
    </span>
  );
}
