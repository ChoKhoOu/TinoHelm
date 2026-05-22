"use client";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type { ReactNode } from "react";

type EmptyVariant = "first-use" | "no-results" | "error" | "not-configured" | "data-cleared";

interface EmptyStateProps {
  variant: EmptyVariant;
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  hint?: ReactNode;
  errorCode?: string;
  /** "full" = page-level (py-16), "section" = card-level (py-10), "table" = inline (py-8) */
  size?: "full" | "section" | "table";
  className?: string;
}

/* QDS spec: single glyph in rounded square — no emoji, no complex illustration */
const defaultGlyphs: Record<EmptyVariant, string> = {
  "first-use":      "⧖",
  "no-results":     "⊘",
  error:            "!",
  "not-configured": "⚙",
  "data-cleared":   "⊡",
};

const iconBg: Record<EmptyVariant, string> = {
  "first-use":      "bg-secondary",
  "no-results":     "bg-secondary",
  error:            "bg-qds-danger-dim",
  "not-configured": "bg-qds-warning-dim",
  "data-cleared":   "bg-secondary",
};

const iconColor: Record<EmptyVariant, string> = {
  "first-use":      "text-muted-foreground",
  "no-results":     "text-muted-foreground",
  error:            "text-destructive",
  "not-configured": "text-qds-warning",
  "data-cleared":   "text-muted-foreground",
};

const btnVariant: Record<EmptyVariant, "default" | "ghost" | "warning"> = {
  "first-use":      "default",
  "no-results":     "ghost",
  error:            "ghost",
  "not-configured": "warning",
  "data-cleared":   "default",
};

const sizePadding = {
  full:    "py-16",
  section: "py-10",
  table:   "py-8",
};

const iconSize = {
  full:    "size-14 text-[1.5rem]",
  section: "size-10 text-[1.15rem]",
  table:   "",  /* no icon for table */
};

export function EmptyState({
  variant,
  icon,
  title,
  description,
  action,
  hint,
  errorCode,
  size = "full",
  className,
}: EmptyStateProps) {
  const showIcon = size !== "table";

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        sizePadding[size],
        className,
      )}
    >
      {showIcon && (
        <div
          className={cn(
            "flex items-center justify-center rounded-[16px] mb-5",
            iconBg[variant],
            iconSize[size],
          )}
        >
          {icon ?? (
            <span className={iconColor[variant]}>{defaultGlyphs[variant]}</span>
          )}
        </div>
      )}

      <h3 className={cn(
        "font-semibold text-foreground",
        size === "section" ? "text-[0.82rem]" : "text-[0.9rem]",
        size === "table" ? "text-[0.78rem]" : "",
      )}>{title}</h3>

      {description && (
        <p className={cn(
          "max-w-[360px] text-muted-foreground leading-relaxed mt-1.5",
          size === "section" ? "text-[0.72rem]" : "text-[0.78rem]",
        )}>
          {description}
        </p>
      )}

      {action && (
        <Button
          variant={btnVariant[variant]}
          size="sm"
          onClick={action.onClick}
          className="mt-4"
        >
          {action.label}
        </Button>
      )}

      {hint && (
        <p className="mt-3 font-mono text-[0.68rem] text-qds-t3">{hint}</p>
      )}

      {errorCode && (
        <p className="mt-3 font-mono text-[0.65rem] text-qds-t3">
          错误码: <span className="text-muted-foreground">{errorCode}</span>
        </p>
      )}
    </div>
  );
}
