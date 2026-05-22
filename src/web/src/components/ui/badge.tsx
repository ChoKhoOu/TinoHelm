import * as React from "react";

const variantStyles: Record<string, string> = {
  success: "bg-qds-success-dim text-qds-success",
  warning: "bg-qds-warning-dim text-qds-warning",
  info: "bg-qds-info-dim text-qds-info",
  error: "bg-qds-danger-dim text-destructive",
  neutral: "bg-secondary text-muted-foreground",
};

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: string;
}

function Badge({ variant = "neutral", className = "", children, ...props }: BadgeProps) {
  const base = "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[.65rem] font-medium";
  const v = variantStyles[variant] ?? variantStyles.neutral;
  return (
    <span className={`${base} ${v} ${className}`} {...props}>
      {children}
    </span>
  );
}

export { Badge };
export type { BadgeProps };
