import { cn } from "@/lib/utils";

type Status = "running" | "done" | "failed" | "queued";

const styles: Record<Status, string> = {
  running: "bg-primary/10 text-primary",
  done: "bg-qds-success-dim text-qds-success",
  failed: "bg-qds-danger-dim text-qds-danger",
  queued: "bg-secondary text-muted-foreground",
};

const labels: Record<Status, string> = {
  running: "Running",
  done: "✓ Done",
  failed: "✕ Failed",
  queued: "◦ Queued",
};

export function StatusBadge({ status, label }: { status: Status; label?: string }) {
  return (
    <span className={cn(
      "inline-flex items-center gap-1.5 font-mono text-[0.68rem] font-medium px-2.5 py-0.5 rounded-full",
      styles[status],
    )}>
      {status === "running" && <PulseRing />}
      {label ?? labels[status]}
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
