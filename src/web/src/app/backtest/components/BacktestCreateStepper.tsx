"use client";

import { cn } from "@/lib/utils";
import { Check } from "lucide-react";
import { STEPPER_DOT_CLS_MAP } from "./backtestStyles";

/* ------------------------------------------------------------------ */
/*  BacktestCreateStepper                                               */
/* ------------------------------------------------------------------ */

interface BacktestCreateStepperProps {
  step: 1 | 2 | 3;
  className?: string;
}

const STEP_LABELS = ["策略与标的", "时间区间", "资金与成本"] as const;

export function BacktestCreateStepper({ step, className }: BacktestCreateStepperProps) {
  return (
    <div className={cn("flex items-center gap-0 px-6 py-4", className)}>
      {STEP_LABELS.map((label, i) => {
        const stepNum = (i + 1) as 1 | 2 | 3;
        const isActive = step === stepNum;
        const isCompleted = step > stepNum;
        const dotCls = isActive
          ? STEPPER_DOT_CLS_MAP.active
          : isCompleted
          ? STEPPER_DOT_CLS_MAP.completed
          : STEPPER_DOT_CLS_MAP.pending;

        return (
          <div key={stepNum} className="flex items-center" style={{ flex: i < 2 ? "1" : "none" }}>
            {/* Step dot + label */}
            <div className="flex flex-col items-center gap-1.5">
              <div
                className={cn(
                  "w-6 h-6 rounded-full flex items-center justify-center font-mono text-[0.65rem] font-semibold transition-colors duration-200",
                  dotCls
                )}
              >
                {isCompleted ? (
                  <Check className="w-3 h-3" />
                ) : (
                  stepNum
                )}
              </div>
              <span
                className={cn(
                  "font-mono text-[0.6rem] whitespace-nowrap transition-colors duration-200",
                  isActive ? "text-primary font-medium" : "text-muted-foreground"
                )}
              >
                {label}
              </span>
            </div>

            {/* Connector line (not after last step) */}
            {i < 2 && (
              <div
                className={cn(
                  "flex-1 h-px mx-2 transition-colors duration-200",
                  isCompleted ? "bg-primary/60" : "bg-border"
                )}
                style={{ marginBottom: "18px" }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
