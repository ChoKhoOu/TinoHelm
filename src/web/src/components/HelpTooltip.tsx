"use client";

import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface HelpTooltipProps {
  content: string;
  className?: string;
}

export function HelpTooltip({ content, className }: HelpTooltipProps) {
  return (
    <Tooltip>
      <TooltipTrigger
        className={cn(
          "inline-flex size-3.5 shrink-0 cursor-help items-center justify-center rounded-full border border-muted-foreground text-[9px] font-semibold leading-none text-muted-foreground transition-colors duration-[var(--dur-fast)] hover:border-qds-t1 hover:text-qds-t1",
          className,
        )}
      >
        ?
      </TooltipTrigger>
      <TooltipContent className="max-w-[240px] text-xs">
        {content}
      </TooltipContent>
    </Tooltip>
  );
}
