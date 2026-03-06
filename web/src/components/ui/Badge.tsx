import { ReactNode } from "react";

type BadgeVariant = "success" | "error" | "warning" | "info" | "connected" | "disconnected" | "neutral";

interface BadgeProps {
  variant?: BadgeVariant;
  children: ReactNode;
  dot?: boolean;
  className?: string;
}

const variantClasses: Record<BadgeVariant, string> = {
  success: "bg-[var(--accent-green-20)] text-[var(--accent-green)]",
  error: "bg-[var(--accent-red-20)] text-[var(--accent-red)]",
  warning: "bg-[var(--accent-orange-20)] text-[var(--accent-orange)]",
  info: "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]",
  connected: "bg-[var(--accent-green-20)] text-[var(--accent-green)]",
  disconnected: "bg-[var(--accent-red-20)] text-[var(--accent-red)]",
  neutral: "bg-[var(--bg-elevated)] text-[var(--text-muted)]",
};

const dotColors: Record<string, string> = {
  connected: "bg-[var(--accent-green)]",
  disconnected: "bg-[var(--accent-red)]",
};

export function Badge({ variant = "success", children, dot, className = "" }: BadgeProps) {
  const showDot = dot || variant === "connected" || variant === "disconnected";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-[10px] py-1 text-[9px] font-bold ${variantClasses[variant]} ${className}`}
    >
      {showDot && (
        <span
          className={`w-1.5 h-1.5 rounded-full ${dotColors[variant] || "bg-current"}`}
        />
      )}
      {children}
    </span>
  );
}
