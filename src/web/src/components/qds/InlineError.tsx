"use client";

export function InlineError({ children }: { children: string }) {
  return (
    <div
      className="flex items-center gap-1.5 font-mono text-xs mt-2 text-destructive animate-qds-fade-up"
    >
      <span>✕</span>
      <span>{children}</span>
    </div>
  );
}
