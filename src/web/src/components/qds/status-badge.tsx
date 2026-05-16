import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export type StatusKind =
  | "running"
  | "done"
  | "completed"
  | "partial_completed"
  | "warning"
  | "failed"
  | "queued"
  | "paused"
  | "flattening"
  | "starting"
  | "cancelling"
  | "cancelled";

const LABEL_MAP_ZH: Record<StatusKind, string> = {
  running:           "运行中",
  done:              "已完成",
  completed:         "已完成",
  partial_completed: "部分完成",
  warning:           "警告",
  failed:            "失败",
  queued:            "排队中",
  paused:            "已暂停",
  flattening:        "平仓中",
  starting:          "启动中",
  cancelling:        "取消中",
  cancelled:         "已取消",
};

const LABEL_MAP_EN: Record<StatusKind, string> = {
  running:           "Running",
  done:              "Done",
  completed:         "Completed",
  partial_completed: "Partial",
  warning:           "Warning",
  failed:            "Failed",
  queued:            "Queued",
  paused:            "Paused",
  flattening:        "Flattening",
  starting:          "Starting",
  cancelling:        "Cancelling",
  cancelled:         "Cancelled",
};

const COLOR_MAP: Record<StatusKind, string> = {
  running:           "bg-primary/10 text-primary",
  done:              "bg-qds-success-dim text-qds-success",
  completed:         "bg-qds-success-dim text-qds-success",
  partial_completed: "bg-qds-warning-dim text-qds-warning",
  warning:           "bg-qds-warning-dim text-qds-warning",
  failed:            "bg-qds-danger-dim text-qds-danger",
  queued:            "bg-secondary text-muted-foreground",
  paused:            "bg-secondary text-muted-foreground",
  flattening:        "bg-qds-info-dim text-qds-info",
  starting:          "bg-qds-info-dim text-qds-info",
  cancelling:        "bg-secondary text-muted-foreground",
  cancelled:         "bg-secondary text-muted-foreground",
};

const FALLBACK_COLOR = "bg-secondary text-muted-foreground";

export function StatusBadge({
  status,
  locale = "zh",
  children,
}: {
  status: StatusKind | (string & {});
  locale?: "zh" | "en";
  children?: ReactNode;
}) {
  const kind = status as StatusKind;
  const labelMap = locale === "zh" ? LABEL_MAP_ZH : LABEL_MAP_EN;
  const label = children ?? (labelMap[kind] ?? status);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 font-mono text-[0.68rem] font-medium px-2.5 py-0.5 rounded-full",
        COLOR_MAP[kind] ?? FALLBACK_COLOR,
      )}
    >
      {kind === "running" && <PulseRing />}
      {label}
    </span>
  );
}

function PulseRing() {
  return (
    <span className="relative w-1.5 h-1.5 rounded-full bg-primary">
      <span className="absolute inset-[-3px] rounded-full border-[1.5px] border-primary animate-qds-pulse opacity-0" />
    </span>
  );
}
