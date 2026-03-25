"use client";

import { Badge } from "@/components/ui/badge";

const STATUS_MAP: Record<string, { variant: "success" | "warning" | "info" | "error" | "neutral"; label: string }> = {
  queued: { variant: "warning", label: "排队中" },
  running: { variant: "info", label: "运行中" },
  completed: { variant: "success", label: "已完成" },
  failed: { variant: "error", label: "失败" },
  cancelling: { variant: "neutral", label: "取消中" },
  cancelled: { variant: "neutral", label: "已取消" },
};

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const key = status.toLowerCase();
  const { variant, label } = STATUS_MAP[key] ?? { variant: "neutral" as const, label: status };
  return (
    <Badge variant={variant} className={className}>
      {label}
    </Badge>
  );
}
