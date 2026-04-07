import { cn } from "@/lib/utils";

interface ShimmerBarProps {
  progress: number;
  height?: "sm" | "md";
  active?: boolean;
  variant?: "accent" | "success" | "danger";
}

export function ShimmerBar({ progress, height = "sm", active = true, variant = "accent" }: ShimmerBarProps) {
  const colors = {
    accent: "bg-primary",
    success: "bg-qds-success",
    danger: "bg-qds-danger",
  };

  return (
    <div className={cn(
      "w-full overflow-hidden rounded-full bg-secondary relative",
      height === "sm" ? "h-[3px]" : "h-1.5",
    )}>
      <div
        className={cn("h-full rounded-full transition-[width] duration-[1.5s] ease-qds", colors[variant])}
        style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
      />
      {active && (
        <div className="absolute inset-0 animate-qds-shimmer">
          <div className="h-full w-full bg-gradient-to-r from-transparent via-white/35 to-transparent" />
        </div>
      )}
    </div>
  );
}
