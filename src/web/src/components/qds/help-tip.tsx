import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export function HelpTip({ text }: { text: string }) {
  return (
    <TooltipProvider delay={200}>
      <Tooltip>
        <TooltipTrigger>
          <span className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border text-[0.55rem] text-qds-t3 cursor-help ml-1 transition-colors hover:text-qds-t1 hover:border-qds-t3 hover:bg-secondary">
            ?
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-[220px] text-xs">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
