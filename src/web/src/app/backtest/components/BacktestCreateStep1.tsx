"use client";

import { useEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import { apiGet } from "@/lib/api";
import { SectionLabel } from "@/components/qds";
import {
  FORM_GROUP_CLS,
  FORM_LABEL_CLS,
  FORM_SECTION_STATIC_CLS,
  parseTimeframe,
} from "./backtestStyles";
import type { Subscription } from "./BacktestSubscriptionTable";

/* ------------------------------------------------------------------ */
/*  Constants                                                           */
/* ------------------------------------------------------------------ */

const DEFAULT_TIMEFRAME = "5m";

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

interface ParamInfo {
  name: string;
  type: string;
  default: string | number | boolean | null;
}

export interface Step1Form {
  strategy_name: string;
}

interface BacktestCreateStep1Props {
  form: Step1Form;
  subscriptions: Subscription[];
  strategyParams: ParamInfo[];
  paramOverrides: Record<string, string>;
  paramsExpanded: boolean;
  onFormChange: (patch: Partial<Step1Form>) => void;
  onSubscriptionsChange: (subs: Subscription[]) => void;
  onParamOverridesChange: (overrides: Record<string, string>) => void;
  onParamsExpandedChange: (expanded: boolean) => void;
}

interface StrategyInfo {
  name: string;
  type?: string;
}

interface BinanceSymbol {
  symbol: string;
  base: string;
  quote: string;
}

/* ------------------------------------------------------------------ */
/*  SymbolPickerWithChips — inline component                           */
/* ------------------------------------------------------------------ */

interface SymbolPickerWithChipsProps {
  subscriptions: Subscription[];
  onSubscriptionsChange: (subs: Subscription[]) => void;
  defaultTimeframe: string;
}

function SymbolPickerWithChips({
  subscriptions,
  onSubscriptionsChange,
  defaultTimeframe,
}: SymbolPickerWithChipsProps) {
  const [allSymbols, setAllSymbols] = useState<BinanceSymbol[]>([]);
  const [searchText, setSearchText] = useState("");
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Fetch Binance symbols on mount.
  useEffect(() => {
    apiGet<BinanceSymbol[]>("/api/data/symbols")
      .then((res) => { if (res) setAllSymbols(res); })
      .catch(() => {});
  }, []);

  // Close dropdown on outside click.
  useEffect(() => {
    if (!dropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (
        inputRef.current && !inputRef.current.contains(e.target as Node) &&
        dropdownRef.current && !dropdownRef.current.contains(e.target as Node)
      ) {
        setDropdownOpen(false);
        setSearchText("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [dropdownOpen]);

  const selectedSymbols = new Set(subscriptions.map((s) => s.symbol));

  const filtered = allSymbols
    .filter((s) => {
      const q = searchText.toUpperCase();
      return !q || s.symbol.includes(q) || s.base.includes(q);
    })
    .slice(0, 50);

  const addSymbol = (symbol: string) => {
    if (selectedSymbols.has(symbol)) return;
    const parsed = parseTimeframe(defaultTimeframe);
    onSubscriptionsChange([
      ...subscriptions,
      {
        exchange: "binance",
        symbol,
        granularity: "bar",
        dataType: "klines",
        timeframe: parsed.clean,
        timeframeValue: parsed.value,
        timeframeUnit: parsed.unit,
        auto: false,
      },
    ]);
    setSearchText("");
    setDropdownOpen(false);
  };

  const removeSymbol = (symbol: string) => {
    onSubscriptionsChange(subscriptions.filter((s) => s.symbol !== symbol));
  };

  return (
    <div className="flex flex-col gap-3">
      {/* Search input + dropdown */}
      <div className="relative">
        <input
          ref={inputRef}
          className="qds-input"
          placeholder="搜索并添加标的..."
          value={searchText}
          onChange={(e) => {
            setSearchText(e.target.value.toUpperCase());
            setDropdownOpen(true);
          }}
          onFocus={() => setDropdownOpen(true)}
        />
        {dropdownOpen && (
          <div
            ref={dropdownRef}
            className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-lg border bg-input shadow-xl animate-in fade-in slide-in-from-top-1 duration-150"
          >
            {filtered.length === 0 ? (
              <div className="px-3 py-2 text-xs text-muted-foreground">无匹配标的</div>
            ) : (
              filtered.map((s) => {
                const isSelected = selectedSymbols.has(s.symbol);
                return (
                  <button
                    key={s.symbol}
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      addSymbol(s.symbol);
                    }}
                    className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-secondary transition-colors ${
                      isSelected ? "text-primary opacity-50 cursor-not-allowed" : "text-foreground"
                    }`}
                  >
                    <span className="font-mono font-medium">{s.symbol}</span>
                    <span className="text-muted-foreground ml-2">{s.base}</span>
                    {isSelected && <Check className="w-3.5 h-3.5 text-primary ml-auto" />}
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>

      {/* Chip list */}
      {subscriptions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {subscriptions.map((sub) => (
            <span
              key={sub.symbol}
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-primary/15 text-primary text-xs font-mono"
            >
              {sub.symbol}
              <button
                type="button"
                onClick={() => removeSymbol(sub.symbol)}
                className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full hover:bg-primary/30 transition-colors text-primary/70 hover:text-primary"
                aria-label={`移除 ${sub.symbol}`}
              >
                <X className="w-2.5 h-2.5" />
              </button>
            </span>
          ))}
        </div>
      )}

      {subscriptions.length === 0 && (
        <div className="text-[0.65rem] text-qds-t3">尚未添加标的</div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  BacktestCreateStep1 — 策略与标的                                   */
/* ------------------------------------------------------------------ */

export function BacktestCreateStep1({
  form,
  subscriptions,
  onFormChange,
  onSubscriptionsChange,
}: BacktestCreateStep1Props) {
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [strategySearch, setStrategySearch] = useState("");
  const [strategyDropdownOpen, setStrategyDropdownOpen] = useState(false);
  const strategyRef = useRef<HTMLDivElement>(null);

  // Fetch strategy list on mount.
  useEffect(() => {
    apiGet<StrategyInfo[]>("/api/strategies")
      .then((res) => { if (res) setStrategies(res); })
      .catch(() => {});
  }, []);

  // Close strategy dropdown on outside click.
  useEffect(() => {
    if (!strategyDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (strategyRef.current && !strategyRef.current.contains(e.target as Node)) {
        setStrategyDropdownOpen(false);
        setStrategySearch("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [strategyDropdownOpen]);

  // Fetch defaults when strategy changes.
  useEffect(() => {
    if (!form.strategy_name) {
      onSubscriptionsChange([]);
      return;
    }
    apiGet<{
      symbols: string[];
      interval: string | null;
      subscriptions?: Array<{
        exchange: string;
        symbol: string;
        granularity: string;
        timeframe: string | null;
        tick_type: string | null;
        auto: boolean;
      }>;
    }>(`/api/strategies/${encodeURIComponent(form.strategy_name)}/defaults`)
      .then((d) => {
        if (!d) return;
        if (d.subscriptions?.length) {
          onSubscriptionsChange(
            d.subscriptions.map((s) => {
              const parsed = parseTimeframe(s.timeframe || DEFAULT_TIMEFRAME);
              return {
                exchange: "binance",
                symbol: s.symbol,
                granularity: (s.granularity as "bar" | "tick") || "bar",
                dataType: s.granularity === "tick" ? "aggTrades" : "klines",
                timeframe: parsed.clean,
                timeframeValue: parsed.value,
                timeframeUnit: parsed.unit,
                tickType: s.tick_type || "trades",
                auto: s.auto,
              };
            }),
          );
        } else if (d.symbols?.length) {
          const parsed = parseTimeframe(d.interval || DEFAULT_TIMEFRAME);
          onSubscriptionsChange(
            d.symbols.map((sym) => ({
              exchange: "binance",
              symbol: sym,
              granularity: "bar" as const,
              dataType: "klines",
              timeframe: parsed.clean,
              timeframeValue: parsed.value,
              timeframeUnit: parsed.unit,
              auto: true,
            })),
          );
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.strategy_name]);

  const filteredStrategies = strategies.filter((s) =>
    s.name.toLowerCase().includes(strategySearch.toLowerCase()),
  );

  const currentTimeframe =
    subscriptions.find((s) => s.granularity === "bar")?.timeframe || DEFAULT_TIMEFRAME;

  return (
    <div className="px-6 py-5 flex flex-col gap-6">
      {/* Strategy selector */}
      <div className={FORM_SECTION_STATIC_CLS}>
        <div className="mb-3"><SectionLabel>策略</SectionLabel></div>
        <div
          ref={strategyRef}
          className={FORM_GROUP_CLS}
          style={strategyDropdownOpen ? { position: "relative", zIndex: 10 } : { position: "relative" }}
        >
          <div className={FORM_LABEL_CLS}>
            策略 <span className="text-destructive text-[0.65rem]">*</span>
          </div>
          <input
            className="qds-input"
            value={strategyDropdownOpen ? strategySearch : form.strategy_name || ""}
            onChange={(e) => {
              setStrategySearch(e.target.value);
              setStrategyDropdownOpen(true);
            }}
            onFocus={() => {
              setStrategySearch("");
              setStrategyDropdownOpen(true);
            }}
            placeholder="搜索策略..."
          />
          {strategyDropdownOpen && (
            <div className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-lg border bg-input shadow-xl animate-in fade-in slide-in-from-top-1 duration-150">
              {filteredStrategies.length === 0 ? (
                <div className="px-3 py-2 text-xs text-muted-foreground">无匹配策略</div>
              ) : (
                filteredStrategies.map((s) => (
                  <button
                    key={s.name}
                    type="button"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      onFormChange({ strategy_name: s.name });
                      setStrategyDropdownOpen(false);
                      setStrategySearch("");
                    }}
                    className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-secondary transition-colors ${
                      form.strategy_name === s.name ? "text-primary" : "text-foreground"
                    }`}
                  >
                    <span className="font-medium">{s.name}</span>
                    <span className="flex items-center gap-1.5">
                      {s.type === "portfolio" && (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-qds-accent-dim text-qds-info font-medium">
                          组合
                        </span>
                      )}
                      {form.strategy_name === s.name && (
                        <Check className="w-3.5 h-3.5 text-primary" />
                      )}
                    </span>
                  </button>
                ))
              )}
            </div>
          )}
          <div className="text-[0.65rem] text-qds-t3 mt-0.5">选择策略后自动填充数据订阅</div>
        </div>
      </div>

      {/* Symbol picker with chips */}
      <div className={FORM_SECTION_STATIC_CLS}>
        <div className="mb-3">
          <SectionLabel>
            标的
            <span className="font-normal text-muted-foreground text-[0.55rem] tracking-normal normal-case">
              {subscriptions.length > 0
                ? ` · ${subscriptions.length} 个`
                : " · 选择策略后自动填充"}
            </span>
          </SectionLabel>
        </div>
        <SymbolPickerWithChips
          subscriptions={subscriptions}
          onSubscriptionsChange={onSubscriptionsChange}
          defaultTimeframe={currentTimeframe}
        />
      </div>
    </div>
  );
}
