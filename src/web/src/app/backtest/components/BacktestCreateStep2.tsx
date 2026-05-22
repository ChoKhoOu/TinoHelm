"use client";

import { useState, useEffect, useRef } from "react";
import { apiPost } from "@/lib/api";
import { InlineError } from "@/components/qds";
import {
  FORM_ROW_CLS,
  FORM_GROUP_CLS,
  FORM_LABEL_CLS,
  TIMEFRAME_CHIP_CLS,
  parseTimeframe,
} from "./backtestStyles";
import type { Subscription } from "./BacktestSubscriptionTable";

/* ------------------------------------------------------------------ */
/*  Constants                                                           */
/* ------------------------------------------------------------------ */

const TIMEFRAME_WHITELIST = /^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d)$/;

const QUICK_CHIPS: { label: string; value: string }[] = [
  { label: "1m", value: "1m" },
  { label: "5m", value: "5m" },
  { label: "15m", value: "15m" },
  { label: "1h", value: "1h" },
  { label: "4h", value: "4h" },
  { label: "1d", value: "1d" },
];

const QUICK_VALUES = new Set(QUICK_CHIPS.map((c) => c.value));

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

export interface Step2Form {
  start_date: string;
  end_date: string;
}

interface BacktestCreateStep2Props {
  form: Step2Form;
  onFormChange: (patch: Partial<Step2Form>) => void;
  subscriptions: Subscription[];
  onSubscriptionsChange: (next: Subscription[]) => void;
}

/* ------------------------------------------------------------------ */
/*  BacktestCreateStep2                                                 */
/* ------------------------------------------------------------------ */

export function BacktestCreateStep2({
  form,
  onFormChange,
  subscriptions,
  onSubscriptionsChange,
}: BacktestCreateStep2Props) {
  // Custom chip state
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [customInputValue, setCustomInputValue] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);
  // Track the last valid timeframe for rollback
  const lastValidTimeframeRef = useRef<string>(subscriptions[0]?.timeframe ?? "5m");

  // Estimate state
  const [estimate, setEstimate] = useState<{ total_bars: number; estimated_label: string } | null>(null);

  // Date validation
  const dateValid =
    !form.start_date || !form.end_date || new Date(form.start_date) < new Date(form.end_date);

  // Current active timeframe
  const activeTimeframe = subscriptions[0]?.timeframe ?? "";

  // Whether the active timeframe is in the quick list
  const isCustomActive = !!activeTimeframe && !QUICK_VALUES.has(activeTimeframe);

  // Custom chip label
  const customChipLabel = isCustomActive ? `自定义 · ${activeTimeframe}` : "自定义";

  // Apply a timeframe to all subscriptions
  const applyTimeframe = (tf: string) => {
    const parsed = parseTimeframe(tf);
    const updated = subscriptions.map((s) => ({
      ...s,
      timeframe: parsed.clean,
      timeframeValue: parsed.value,
      timeframeUnit: parsed.unit,
    }));
    onSubscriptionsChange(updated);
    lastValidTimeframeRef.current = parsed.clean;
  };

  // Handle quick chip click
  const handleChipClick = (value: string) => {
    setShowCustomInput(false);
    setCustomError(null);
    applyTimeframe(value);
  };

  // Handle custom chip click
  const handleCustomChipClick = () => {
    setShowCustomInput(true);
    setCustomError(null);
    setCustomInputValue(isCustomActive ? activeTimeframe : "");
  };

  // Handle custom input blur — validate and apply
  const handleCustomInputBlur = () => {
    const val = customInputValue.trim();
    if (!val) {
      // No input — hide the box without changing selection
      setShowCustomInput(false);
      setCustomError(null);
      return;
    }
    if (!TIMEFRAME_WHITELIST.test(val)) {
      setCustomError("仅支持 1m/3m/5m/15m/30m/1h/2h/4h/6h/8h/12h/1d");
      // Rollback: do not call onSubscriptionsChange
      return;
    }
    // Validation passed
    setCustomError(null);
    setShowCustomInput(false);
    applyTimeframe(val);
  };

  // 300ms debounced estimate fetch
  useEffect(() => {
    const symbols = [...new Set(subscriptions.map((s) => s.symbol))];

    if (symbols.length === 0 || !form.start_date || !form.end_date || !dateValid) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: reset estimate when inputs become invalid
      setEstimate(null);
      return;
    }

    const timer = setTimeout(() => {
      apiPost<{ total_bars: number; estimated_label: string }>("/api/backtest/estimate", {
        start_date: form.start_date,
        end_date: form.end_date,
        symbols,
      })
        .then((d) => d && setEstimate(d))
        .catch(() => setEstimate(null));
    }, 300);

    return () => clearTimeout(timer);
  }, [subscriptions, form.start_date, form.end_date, dateValid]);

  const chipBase =
    "inline-flex items-center font-mono text-[0.7rem] px-3 py-1 rounded-md border cursor-pointer transition-colors duration-150 ease-qds hover:opacity-90";

  return (
    <div className="flex flex-col gap-6 px-6 py-5">
      {/* Timeframe chips */}
      <div className={FORM_GROUP_CLS}>
        <div className={FORM_LABEL_CLS}>周期</div>
        <div className="flex flex-wrap gap-2">
          {QUICK_CHIPS.map((chip) => {
            const isActive = activeTimeframe === chip.value;
            return (
              <button
                key={chip.value}
                type="button"
                className={`${chipBase} ${isActive ? TIMEFRAME_CHIP_CLS.active : TIMEFRAME_CHIP_CLS.inactive}`}
                onClick={() => handleChipClick(chip.value)}
              >
                {chip.label}
              </button>
            );
          })}
          {/* Custom chip */}
          <button
            type="button"
            className={`${chipBase} ${isCustomActive ? TIMEFRAME_CHIP_CLS.active : TIMEFRAME_CHIP_CLS.inactive}`}
            onClick={handleCustomChipClick}
          >
            {customChipLabel}
          </button>
        </div>

        {/* Custom input (shown when custom chip clicked) */}
        {showCustomInput && (
          <div className="flex flex-col gap-1 mt-2">
            <input
              type="text"
              autoFocus
              className="qds-input w-36"
              placeholder="e.g. 30m"
              value={customInputValue}
              onChange={(e) => {
                setCustomInputValue(e.target.value);
                setCustomError(null);
              }}
              onBlur={handleCustomInputBlur}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.currentTarget.blur();
                }
                if (e.key === "Escape") {
                  setShowCustomInput(false);
                  setCustomError(null);
                }
              }}
            />
            {customError && (
              <InlineError variant="error">{customError}</InlineError>
            )}
          </div>
        )}
      </div>

      {/* Date range */}
      <div className={FORM_ROW_CLS}>
        <div className={FORM_GROUP_CLS}>
          <div className={FORM_LABEL_CLS}>
            开始日期 <span className="text-destructive text-[0.65rem]">*</span>
          </div>
          <input
            className="qds-input"
            type="date"
            value={form.start_date}
            onChange={(e) => onFormChange({ start_date: e.target.value })}
          />
        </div>
        <div className={FORM_GROUP_CLS}>
          <div className={FORM_LABEL_CLS}>
            结束日期 <span className="text-destructive text-[0.65rem]">*</span>
          </div>
          <input
            className="qds-input"
            type="date"
            value={form.end_date}
            onChange={(e) => onFormChange({ end_date: e.target.value })}
          />
          {!dateValid && (
            <InlineError variant="error">结束日期必须晚于开始日期</InlineError>
          )}
        </div>
      </div>

      {/* K-line estimate */}
      {(estimate || (!dateValid && form.start_date && form.end_date)) && (
        <div className="font-mono text-[0.72rem] text-muted-foreground">
          {dateValid && estimate ? (
            <>
              预估运行时间{" "}
              <span className="text-primary">{estimate.estimated_label}</span>
              {estimate.total_bars != null &&
                ` · 约 ${(estimate.total_bars / 1_000_000).toFixed(1)}M bars`}
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
