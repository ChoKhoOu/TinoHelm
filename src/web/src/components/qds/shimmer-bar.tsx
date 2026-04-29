import { cn } from "@/lib/utils";

export type ShimmerStage =
  | "aligning"
  | "computing"
  | "evaluating"
  | "persisting"
  | null;

const STAGE_DEFS: { key: Exclude<ShimmerStage, null>; label: string }[] = [
  { key: "aligning",   label: "对齐数据" },
  { key: "computing",  label: "计算" },
  { key: "evaluating", label: "评估" },
  { key: "persisting", label: "持久化" },
];

interface ShimmerBarProps {
  progress: number;
  height?: "sm" | "md";
  active?: boolean;
  variant?: "accent" | "success" | "danger";
  /** Current active stage (only used when showStages=true). */
  stage?: ShimmerStage;
  /** Show 4-stage tab row below the bar. Default false (backward compat). */
  showStages?: boolean;
}

export function ShimmerBar({
  progress,
  height = "sm",
  active = true,
  variant = "accent",
  stage = null,
  showStages = false,
}: ShimmerBarProps) {
  const colors = {
    accent: "bg-primary",
    success: "bg-qds-success",
    danger: "bg-qds-danger",
  };

  const clampedPct = Math.min(100, Math.max(0, progress));
  const activeIdx = STAGE_DEFS.findIndex((s) => s.key === stage);

  return (
    <div className={cn("w-full", showStages && "space-y-2")}>
      {/* Bar */}
      <div
        className={cn(
          "w-full overflow-hidden rounded-full bg-secondary relative",
          height === "sm" ? "h-[3px]" : "h-1.5",
        )}
      >
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-[1.5s] ease-qds",
            colors[variant],
          )}
          style={{ width: `${clampedPct}%` }}
        />
        {active && (
          <div className="absolute inset-0 animate-qds-shimmer">
            <div className="h-full w-full bg-gradient-to-r from-transparent via-white/35 to-transparent" />
          </div>
        )}
      </div>

      {/* 4-stage tab row */}
      {showStages && (
        <div className="flex items-center gap-1.5 text-[0.6rem]">
          {STAGE_DEFS.map((s, idx) => {
            const isActive = stage === s.key;
            const isPast = activeIdx >= 0 && idx < activeIdx;
            return (
              <div
                key={s.key}
                data-stage={s.key}
                className={cn(
                  "flex flex-1 items-center justify-center rounded-sm border px-1.5 py-0.5 font-mono uppercase tracking-wide gap-1",
                  isActive
                    ? "border-primary bg-qds-accent-dim text-primary"
                    : isPast
                    ? "border-border bg-card text-muted-foreground"
                    : "border-border bg-card text-muted-foreground/40",
                )}
              >
                {isActive && (
                  <span className="inline-block h-1 w-1 shrink-0 animate-pulse rounded-full bg-primary" />
                )}
                {s.label}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
