"use client";

interface InlineErrorProps {
  children: string;
  variant?: "error" | "hint";
}

export function InlineError({ children, variant = "error" }: InlineErrorProps) {
  const colorCls = variant === "hint" ? "text-muted-foreground" : "text-destructive";
  const icon = variant === "hint" ? "–" : "✕";
  return (
    <div
      className={`flex items-center gap-1.5 font-mono text-xs mt-2 animate-qds-fade-up ${colorCls}`}
    >
      <span>{icon}</span>
      <span>{children}</span>
    </div>
  );
}
