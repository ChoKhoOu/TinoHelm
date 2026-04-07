"use client";

import { useCallback, useState } from "react";
import { cn } from "@/lib/utils";
import { Check, Copy } from "lucide-react";

interface IdBadgeProps {
  id: string;
  className?: string;
}

export function IdBadge({ id, className }: IdBadgeProps) {
  const [copied, setCopied] = useState(false);
  const truncated = id.slice(0, 8);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [id]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={id}
      className={cn(
        "group/id inline-flex items-center gap-1 rounded-sm bg-secondary px-1.5 py-0.5 font-mono text-[0.68rem] text-qds-t1 transition-colors duration-[var(--dur-fast)] hover:bg-qds-border-hover",
        className,
      )}
    >
      <span>{truncated}</span>
      {copied ? (
        <Check className="size-3 text-qds-success" />
      ) : (
        <Copy className="size-3 opacity-0 transition-opacity duration-[var(--dur-fast)] group-hover/id:opacity-100" />
      )}
    </button>
  );
}
