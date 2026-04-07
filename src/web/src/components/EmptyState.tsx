"use client";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { AlertCircle, Inbox, Search } from "lucide-react";
import type { ReactNode } from "react";

interface EmptyStateProps {
  variant: "first-use" | "no-results" | "error";
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  hint?: string;
  className?: string;
}

const defaultIcons: Record<EmptyStateProps["variant"], ReactNode> = {
  "first-use": <Inbox className="size-6 text-muted-foreground" />,
  "no-results": <Search className="size-6 text-muted-foreground" />,
  error: <AlertCircle className="size-6 text-destructive" />,
};

export function EmptyState({
  variant,
  icon,
  title,
  description,
  action,
  hint,
  className,
}: EmptyStateProps) {
  const resolvedIcon = icon ?? defaultIcons[variant];

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 py-16 text-center",
        className,
      )}
    >
      <div
        className={cn(
          "flex size-14 items-center justify-center rounded-[16px]",
          variant === "error" ? "bg-qds-danger-dim" : "bg-secondary",
        )}
      >
        {resolvedIcon}
      </div>

      <h3 className="text-[0.9rem] font-semibold text-foreground">{title}</h3>

      {description && (
        <p className="max-w-[320px] text-[0.78rem] text-muted-foreground">
          {description}
        </p>
      )}

      {action && (
        <Button
          variant={variant === "error" ? "destructive" : "default"}
          size="sm"
          onClick={action.onClick}
          className="mt-1"
        >
          {action.label}
        </Button>
      )}

      {hint && (
        <p className="text-[0.68rem] text-qds-t3">{hint}</p>
      )}
    </div>
  );
}
