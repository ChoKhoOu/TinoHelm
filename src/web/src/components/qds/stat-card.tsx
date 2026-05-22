import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  trend?: "up" | "down" | "neutral";
  help?: string;
  className?: string;
}

export function StatCard({ label, value, sub, trend, help, className }: StatCardProps) {
  return (
    <div className={cn(
      "rounded-lg border bg-card p-4 transition-colors duration-150 ease-qds hover:border-qds-border-hover",
      className
    )}>
      <div className="text-[0.65rem] uppercase tracking-wider text-muted-foreground flex items-center gap-1">
        {label}
        {help && <span className="text-qds-t3 cursor-help" title={help}>?</span>}
      </div>
      <div className={cn(
        "font-mono text-xl font-semibold mt-1",
        trend === "up" && "text-qds-success",
        trend === "down" && "text-qds-danger",
      )}>
        {value}
      </div>
      {sub && (
        <div className={cn(
          "font-mono text-[0.65rem] mt-0.5",
          trend === "up" && "text-qds-success",
          trend === "down" && "text-qds-danger",
          !trend && "text-muted-foreground",
        )}>
          {sub}
        </div>
      )}
    </div>
  );
}
