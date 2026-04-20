import type { ReactNode } from "react";

interface ChartCardProps {
  title: string;
  sub?: string;
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Internal chart card header/body for the report tabs. */
export function ChartCard({ title, sub, badge, children, className }: ChartCardProps) {
  return (
    <div
      className={`rounded-lg border bg-card overflow-hidden transition-colors duration-150 hover:border-qds-border-hover ${className || ""}`}
    >
      <div className="flex justify-between items-center px-3 py-2.5 border-b text-[0.72rem] font-semibold">
        <span>{title}</span>
        <span className="font-mono text-[0.58rem] font-normal text-muted-foreground">
          {badge || sub || ""}
        </span>
      </div>
      <div className="p-3">{children}</div>
    </div>
  );
}

interface VerdictBadgeProps {
  status: string;
}

/**
 * Tab-bar verdict pill. Distinct from the `VerdictBadge` in the factor-research exploration
 * list (which accepts null and renders em-dash); this one always receives an explicit status.
 */
export function VerdictBadge({ status }: VerdictBadgeProps) {
  const cls: Record<string, string> = {
    pass: "bg-qds-success-dim text-qds-success",
    warn: "bg-qds-warning-dim text-qds-warning",
    fail: "bg-qds-danger-dim text-destructive",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 font-mono text-[10px] font-medium px-2 py-0.5 rounded-full ${cls[status] || ""}`}
    >
      {status === "pass" ? "\u2713" : status === "warn" ? "\u26A0" : "\u2717"}
    </span>
  );
}
