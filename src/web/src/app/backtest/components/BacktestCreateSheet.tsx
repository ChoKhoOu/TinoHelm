"use client";

import { useEffect, useState } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetFooter,
  SheetTitle,
} from "@/components/ui/sheet";
import { InlineError } from "@/components/qds";
import { apiGet } from "@/lib/api";
import { BacktestCreateStepper } from "./BacktestCreateStepper";
import { BacktestCreateStep1, type Step1Form } from "./BacktestCreateStep1";
import { BacktestCreateStep2, type Step2Form } from "./BacktestCreateStep2";
import { BacktestCreateStep3, type Step3Form } from "./BacktestCreateStep3";
import type { Subscription } from "./BacktestSubscriptionTable";
import type { BacktestRunSummary } from "./BacktestListView";

/* ------------------------------------------------------------------ */
/*  Local types                                                         */
/* ------------------------------------------------------------------ */

interface ParamInfo {
  name: string;
  type: string;
  default: string | number | boolean | null;
}

interface ParamsResponse {
  name: string;
  config_params: Array<{ name: string; type: string; default: string | number | boolean | null }>;
  optimize_ranges: Record<string, unknown>;
}

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

interface BacktestCreateSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  retryPrefill: BacktestRunSummary | null;
  onSubmit: () => void;
}

/* ------------------------------------------------------------------ */
/*  Default form values                                                 */
/* ------------------------------------------------------------------ */

const DEFAULT_STEP1_FORM: Step1Form = {
  strategy_name: "",
};

const DEFAULT_STEP2_FORM: Step2Form = {
  start_date: "2026-01-01",
  end_date: "2026-03-31",
};

const DEFAULT_STEP3_FORM: Step3Form = {
  initial_capital: "100,000",
  maker_fee: "0.02%",
  taker_fee: "0.05%",
  latency_mode: "fixed",
  latency_ms: "5",
  fill_model_type: "one_tick_slippage",
  prob_fill_on_limit: "1.0",
  prob_slippage: "0.0",
  warmup_bars: "200",
  tags: "",
};

/* ------------------------------------------------------------------ */
/*  BacktestCreateSheet — Sheet container + cross-step state owner     */
/* ------------------------------------------------------------------ */

export function BacktestCreateSheet({
  open,
  onOpenChange,
  retryPrefill,
  onSubmit,
}: BacktestCreateSheetProps) {
  const [step, setStep] = useState<1 | 2 | 3>(1);

  // Step 1 state
  const [step1Form, setStep1Form] = useState<Step1Form>(DEFAULT_STEP1_FORM);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [strategyParams, setStrategyParams] = useState<ParamInfo[]>([]);
  const [paramOverrides, setParamOverrides] = useState<Record<string, string>>({});
  const [paramsExpanded, setParamsExpanded] = useState(false);

  // Step 2 state
  const [step2Form, setStep2Form] = useState<Step2Form>(DEFAULT_STEP2_FORM);

  // Step 3 state
  const [step3Form, setStep3Form] = useState<Step3Form>(DEFAULT_STEP3_FORM);
  const [advancedExpanded, setAdvancedExpanded] = useState(false);

  // fromRetry flag
  const fromRetry = retryPrefill !== null;

  // Prefill from retryPrefill (FR-033: strategy_name, subscriptions, start_date/end_date only)
  useEffect(() => {
    if (!retryPrefill) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: explicit prefill on retry flow, key-based remount ensures step resets
    setStep1Form({ strategy_name: retryPrefill.strategy_name });

    // Prefill subscription from run's symbol + interval
    if (retryPrefill.symbol && retryPrefill.interval) {
      const prefillSub: Subscription = {
        exchange: "BINANCE",
        symbol: retryPrefill.symbol,
        granularity: "bar",
        timeframe: retryPrefill.interval,
        auto: false,
      };
      setSubscriptions([prefillSub]);
    }

    // Prefill dates (step 2)
    setStep2Form((prev) => ({
      ...prev,
      start_date: retryPrefill.start_date ?? prev.start_date,
      end_date: retryPrefill.end_date ?? prev.end_date,
    }));

    // Do NOT prefill capital/fee params (step 3) — per FR-033
  }, [retryPrefill]);

  // FIX-H4: Auto-load strategy params when strategy_name changes
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: clear params on strategy switch
    if (!step1Form.strategy_name) { setStrategyParams([]); return; }
    apiGet<ParamsResponse>(`/api/strategies/${encodeURIComponent(step1Form.strategy_name)}/params`)
      .then((d) => {
        const params = d?.config_params;
        setStrategyParams(Array.isArray(params) ? params : []);
      })
      .catch(() => setStrategyParams([]));
  }, [step1Form.strategy_name]);

  // Reset state when sheet closes
  useEffect(() => {
    if (!open) {
      // Delay reset so close animation plays first
      const t = setTimeout(() => {
        setStep(1);
        setStep1Form(DEFAULT_STEP1_FORM);
        setSubscriptions([]);
        setStrategyParams([]);
        setParamOverrides({});
        setParamsExpanded(false);
        setStep2Form(DEFAULT_STEP2_FORM);
        setStep3Form(DEFAULT_STEP3_FORM);
        setAdvancedExpanded(false);
      }, 300);
      return () => clearTimeout(t);
    }
  }, [open]);

  const handlePrevious = () => {
    if (step === 1) {
      onOpenChange(false);
    } else {
      setStep((s) => (s - 1) as 1 | 2 | 3);
    }
  };

  // FIX-H3: Step validation guards
  const isStep1Valid = step1Form.strategy_name.trim() !== "" && subscriptions.length > 0;
  const isStep2Valid =
    !!step2Form.start_date &&
    !!step2Form.end_date &&
    new Date(step2Form.start_date) < new Date(step2Form.end_date);

  const handleNext = () => {
    if (step === 1 && !isStep1Valid) return;
    if (step === 2 && !isStep2Valid) return;
    if (step < 3) setStep((s) => (s + 1) as 1 | 2 | 3);
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-[520px] p-0 gap-0 flex flex-col"
        showCloseButton={false}
      >
        {/* Header */}
        <SheetHeader className="border-b border-border px-6 py-4 flex-shrink-0 animate-qds-fade-up [animation-delay:0ms]">
          <SheetTitle className="text-base font-semibold text-foreground">
            创建回测
          </SheetTitle>
        </SheetHeader>

        {/* Retry hint banner */}
        {fromRetry && (
          <div className="px-6 pt-3">
            <InlineError variant="hint">
              已复制策略、标的、周期与时间区间，请确认资金与成本参数
            </InlineError>
          </div>
        )}

        {/* Stepper */}
        <BacktestCreateStepper step={step} className="border-b border-border flex-shrink-0 animate-qds-fade-up [animation-delay:80ms]" />

        {/* Step body — animation triggered by step change */}
        <div className="flex-1 overflow-y-auto animate-qds-fade-up [animation-delay:160ms]">
          {step === 1 && (
            <div style={{ animationName: "slideInUp", animationDuration: "250ms", animationFillMode: "both", animationTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)" }}>
              <BacktestCreateStep1
                form={step1Form}
                subscriptions={subscriptions}
                strategyParams={strategyParams}
                paramOverrides={paramOverrides}
                paramsExpanded={paramsExpanded}
                onFormChange={(patch) => setStep1Form((prev) => ({ ...prev, ...patch }))}
                onSubscriptionsChange={setSubscriptions}
                onParamOverridesChange={setParamOverrides}
                onParamsExpandedChange={setParamsExpanded}
              />
            </div>
          )}
          {step === 2 && (
            <div style={{ animationName: "slideInUp", animationDuration: "250ms", animationFillMode: "both", animationTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)" }}>
              <BacktestCreateStep2
                form={step2Form}
                onFormChange={(patch) => setStep2Form((prev) => ({ ...prev, ...patch }))}
                subscriptions={subscriptions}
                onSubscriptionsChange={setSubscriptions}
              />
            </div>
          )}
          {step === 3 && (
            <div style={{ animationName: "slideInUp", animationDuration: "250ms", animationFillMode: "both", animationTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)" }}>
              <BacktestCreateStep3
                form={step3Form}
                onFormChange={(patch) => setStep3Form((prev) => ({ ...prev, ...patch }))}
                strategy_name={step1Form.strategy_name}
                start_date={step2Form.start_date}
                end_date={step2Form.end_date}
                subscriptions={subscriptions}
                onSubscriptionsChange={setSubscriptions}
                strategyParams={strategyParams}
                paramOverrides={paramOverrides}
                paramsExpanded={paramsExpanded}
                onParamOverridesChange={setParamOverrides}
                onParamsExpandedChange={setParamsExpanded}
                advancedExpanded={advancedExpanded}
                onAdvancedExpandedChange={setAdvancedExpanded}
                onSubmit={onSubmit}
                onOpenChange={onOpenChange}
              />
            </div>
          )}
        </div>

        {/* Footer navigation */}
        <SheetFooter className="border-t border-border px-6 py-4 flex-shrink-0 flex flex-row justify-between gap-3 animate-qds-fade-up [animation-delay:240ms]">
          <button
            onClick={handlePrevious}
            className="inline-flex items-center gap-1 font-mono text-[0.72rem] px-4 py-2 rounded-md border border-border bg-transparent text-qds-t1 cursor-pointer transition-all hover:border-qds-border-hover hover:text-foreground hover:bg-secondary"
          >
            {step === 1 ? "取消" : "上一步"}
          </button>

          {step < 3 && (
            <button
              onClick={handleNext}
              disabled={(step === 1 && !isStep1Valid) || (step === 2 && !isStep2Valid)}
              className="inline-flex items-center gap-1 font-mono text-[0.72rem] px-4 py-2 rounded-md border border-primary bg-primary/15 text-primary cursor-pointer transition-all hover:bg-primary/20 disabled:opacity-40 disabled:pointer-events-none"
            >
              下一步
            </button>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
