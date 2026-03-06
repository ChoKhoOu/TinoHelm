import { ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  padding?: boolean;
}

export function Card({ children, className = "", padding = true }: CardProps) {
  return (
    <div
      className={`rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] ${
        padding ? "p-5" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
  change?: string;
  changeType?: "positive" | "negative" | "neutral";
  className?: string;
}

export function MetricCard({
  label,
  value,
  change,
  changeType = "neutral",
  className = "",
}: MetricCardProps) {
  const changeColor =
    changeType === "positive"
      ? "text-[var(--accent-green)]"
      : changeType === "negative"
        ? "text-[var(--accent-red)]"
        : "text-[var(--text-muted)]";

  return (
    <Card className={className}>
      <div className="flex flex-col gap-2">
        <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
          {label}
        </span>
        <div className="flex items-end gap-2">
          <span className="font-heading text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            {value}
          </span>
          {change && (
            <span className={`text-[11px] font-medium ${changeColor}`}>
              {change}
            </span>
          )}
        </div>
      </div>
    </Card>
  );
}
