"use client";

import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { SectionLabel } from "@/components/qds";
import { FORM_SECTION_STATIC_CLS } from "./backtestStyles";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface Subscription {
  exchange: string;
  symbol: string;
  granularity: "bar" | "tick";
  dataType?: string;
  timeframe?: string;
  timeframeValue?: number;
  timeframeUnit?: string;
  tickType?: string;
  depth?: number;
  snapMs?: number;
  auto: boolean;
}

interface BinanceSymbol {
  symbol: string;
  base: string;
  quote: string;
}

/* ------------------------------------------------------------------ */
/*  BacktestSubscriptionTable                                          */
/* ------------------------------------------------------------------ */

interface BacktestSubscriptionTableProps {
  subscriptions: Subscription[];
  onSubscriptionsChange: (next: Subscription[] | ((prev: Subscription[]) => Subscription[])) => void;
}

export function BacktestSubscriptionTable({ subscriptions, onSubscriptionsChange }: BacktestSubscriptionTableProps) {
  const [allSymbols, setAllSymbols] = useState<BinanceSymbol[]>([]);
  const [symbolDropdownIdx, setSymbolDropdownIdx] = useState<number | null>(null);
  const [symbolSearchText, setSymbolSearchText] = useState("");

  // Fetch Binance symbols on mount.
  useEffect(() => {
    apiGet<BinanceSymbol[]>("/api/data/symbols")
      .then((res) => { if (res) setAllSymbols(res); })
      .catch(() => {});
  }, []);

  const setSubscriptions = (updater: Subscription[] | ((prev: Subscription[]) => Subscription[])) => {
    onSubscriptionsChange(updater);
  };

  return (
    <div className={FORM_SECTION_STATIC_CLS}>
      <SectionLabel>
        数据订阅
        <span className="font-normal text-muted-foreground text-[0.55rem] tracking-normal normal-case">
          {subscriptions.length > 0 ? `· ${subscriptions.length} 个数据源` : "· 选择策略后自动填充"}
        </span>
      </SectionLabel>
      <div className="bg-card border border-border rounded-lg overflow-hidden">
        {subscriptions.length === 0 ? (
          <div className="py-10 px-8 text-center">
            <div className="text-[1.2rem] text-qds-t3 mb-1.5">⧖</div>
            <div className="text-[0.78rem] text-muted-foreground mb-0.5">选择策略后自动填充数据订阅</div>
            <div className="text-[0.68rem] text-qds-t3">策略定义需要订阅的交易所、品种和数据粒度</div>
          </div>
        ) : (
          <>
            <table className="w-full border-collapse font-mono text-[0.8rem]">
              <thead>
                <tr>
                  <th className="text-[0.7rem] font-medium text-muted-foreground py-2.5 px-5 tracking-[0.08em] border-b border-border text-left uppercase">品种</th>
                  <th className="text-[0.7rem] font-medium text-muted-foreground py-2.5 px-5 tracking-[0.08em] border-b border-border text-left uppercase">数据类型</th>
                  <th className="text-[0.7rem] font-medium text-muted-foreground py-2.5 px-5 tracking-[0.08em] border-b border-border text-left uppercase">周期</th>
                  <th className="text-[0.7rem] font-medium text-muted-foreground py-2.5 px-5 tracking-[0.08em] border-b border-border w-8" />
                </tr>
              </thead>
              <tbody>
                {subscriptions.map((sub, idx) => {
                  const ctrlStyle: React.CSSProperties = {
                    padding: ".5rem .75rem",
                    background: "var(--bg-in)",
                    border: "1px solid var(--bd)",
                    borderRadius: "var(--rs)",
                    color: "var(--t0)",
                    outline: "none",
                    transition: "border-color 150ms, box-shadow 300ms",
                  };
                  const updateSub = (patch: Partial<Subscription>) => {
                    setSubscriptions((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch, auto: false } : s)));
                  };
                  return (
                    <tr key={idx} className="border-b border-border transition-colors hover:bg-secondary">
                      {/* 品种 */}
                      <td className="py-2.5 px-5 align-middle relative">
                        <div className="flex items-center gap-2">
                          <span className="text-[0.65rem] text-qds-t3 font-normal shrink-0">Binance</span>
                          <input
                            className="font-mono text-[0.82rem]"
                            style={{ ...ctrlStyle, cursor: "text", width: "150px" }}
                            value={symbolDropdownIdx === idx ? symbolSearchText : sub.symbol}
                            onChange={(e) => { setSymbolSearchText(e.target.value.toUpperCase()); setSymbolDropdownIdx(idx); }}
                            onFocus={(e) => { setSymbolSearchText(sub.symbol); setSymbolDropdownIdx(idx); e.currentTarget.style.borderColor = "var(--acc)"; e.currentTarget.style.boxShadow = "0 0 0 3px var(--acc-d)"; }}
                            onBlur={(e) => {
                              e.currentTarget.style.borderColor = "var(--bd)"; e.currentTarget.style.boxShadow = "none";
                              setTimeout(() => {
                                if (symbolDropdownIdx === idx) {
                                  if (symbolSearchText.trim()) updateSub({ symbol: symbolSearchText.trim() });
                                  setSymbolDropdownIdx(null);
                                }
                              }, 200);
                            }}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                if (symbolSearchText.trim()) updateSub({ symbol: symbolSearchText.trim() });
                                setSymbolDropdownIdx(null);
                                (e.target as HTMLInputElement).blur();
                              }
                            }}
                            placeholder="搜索品种..."
                          />
                        </div>
                        {symbolDropdownIdx === idx && (
                          <div
                            className="absolute z-50 max-h-[220px] overflow-y-auto w-[240px] bg-card border border-border rounded-[10px] mt-1 p-1.5 shadow-2xl"
                            style={{ top: "100%", left: "1.25rem" }}
                          >
                            {allSymbols
                              .filter((s) => {
                                const q = symbolSearchText.toUpperCase();
                                return !q || s.symbol.includes(q) || s.base.includes(q);
                              })
                              .slice(0, 50)
                              .map((s) => (
                                <button
                                  key={s.symbol}
                                  type="button"
                                  className={`w-full text-left px-2.5 py-1.5 font-mono text-[0.78rem] border-none rounded-md text-foreground cursor-pointer block transition-all duration-150 hover:bg-secondary hover:translate-x-[3px] ${s.symbol === sub.symbol ? "bg-secondary" : "bg-transparent"}`}
                                  onMouseDown={(e) => {
                                    e.preventDefault();
                                    updateSub({ symbol: s.symbol });
                                    setSymbolDropdownIdx(null);
                                  }}
                                >
                                  <span className="font-medium">{s.symbol}</span>
                                  <span className="text-[0.65rem] text-qds-t3 ml-2">{s.base}</span>
                                </button>
                              ))}
                            {allSymbols.filter((s) => { const q = symbolSearchText.toUpperCase(); return !q || s.symbol.includes(q) || s.base.includes(q); }).length === 0 && (
                              <div className="px-2.5 py-2 text-[0.72rem] text-qds-t3">无匹配 — 按 Enter 使用自定义值</div>
                            )}
                          </div>
                        )}
                      </td>
                      {/* 数据类型 */}
                      <td className="py-2.5 px-5 align-middle">
                        <div className="flex items-center gap-1.5">
                          <select
                            className="font-mono text-[0.82rem]"
                            style={{ ...ctrlStyle, cursor: "pointer", padding: ".5rem .6rem" }}
                            value={sub.granularity}
                            onChange={(e) => {
                              const g = e.target.value as "bar" | "tick";
                              updateSub({ granularity: g, dataType: g === "bar" ? "klines" : "aggTrades" });
                            }}
                          >
                            <option value="bar">Bar</option>
                            <option value="tick">Tick</option>
                          </select>
                          <span className="text-qds-t3 text-[0.7rem]">·</span>
                          <select
                            className="font-mono text-[0.82rem]"
                            style={{ ...ctrlStyle, cursor: "pointer", padding: ".5rem .6rem" }}
                            value={sub.dataType || (sub.granularity === "bar" ? "klines" : "aggTrades")}
                            onChange={(e) => updateSub({ dataType: e.target.value })}
                          >
                            {sub.granularity === "bar" ? (
                              <>
                                <option value="klines">klines</option>
                                <option value="markPriceKlines">markPrice</option>
                                <option value="indexPriceKlines">indexPrice</option>
                                <option value="premiumIndexKlines">premiumIndex</option>
                              </>
                            ) : (
                              <>
                                <option value="aggTrades">aggTrades</option>
                                <option value="trades">trades</option>
                              </>
                            )}
                          </select>
                        </div>
                      </td>
                      {/* 周期 */}
                      <td className="py-2.5 px-5 align-middle">
                        <div className="flex items-center gap-2">
                          {sub.auto && (
                            <span className="font-mono text-[0.6rem] px-1.5 py-0.5 rounded bg-qds-info-dim text-qds-info shrink-0">auto</span>
                          )}
                          {sub.granularity === "bar" ? (
                            <span className="inline-flex items-center bg-input border border-border rounded-md overflow-hidden transition-all">
                              <input
                                type="number"
                                min={1}
                                step={1}
                                value={sub.timeframeValue ?? 5}
                                onChange={(e) => {
                                  const v = Math.max(1, Math.round(Number(e.target.value) || 1));
                                  const unit = sub.timeframeUnit || "m";
                                  updateSub({ timeframeValue: v, timeframeUnit: unit, timeframe: `${v}${unit}` });
                                }}
                                onFocus={(e) => { const p = e.currentTarget.parentElement as HTMLElement; p.style.borderColor = "var(--acc)"; p.style.boxShadow = "0 0 0 3px var(--acc-d)"; }}
                                onBlur={(e) => { const p = e.currentTarget.parentElement as HTMLElement; p.style.borderColor = "var(--bd)"; p.style.boxShadow = "none"; }}
                                className="w-10 text-center border-0 bg-transparent px-0.5 py-1.5 font-mono text-[0.82rem] font-medium text-foreground outline-none"
                                style={{ MozAppearance: "textfield" as never }}
                              />
                              <span className="w-px self-stretch bg-border shrink-0" />
                              <select
                                className="border-0 bg-transparent py-1.5 pl-1.5 pr-2 font-mono text-[0.82rem] font-medium text-foreground outline-none cursor-pointer"
                                value={sub.timeframeUnit || "m"}
                                onChange={(e) => {
                                  const unit = e.target.value;
                                  const v = sub.timeframeValue ?? 5;
                                  updateSub({ timeframeUnit: unit, timeframe: `${v}${unit}` });
                                }}
                              >
                                <option value="s">秒</option>
                                <option value="m">分</option>
                                <option value="h">时</option>
                                <option value="d">天</option>
                              </select>
                            </span>
                          ) : (
                            <select
                              className="font-mono text-[0.82rem]"
                              style={{ ...ctrlStyle, cursor: "pointer", padding: ".5rem .6rem" }}
                              value={sub.tickType || "trades"}
                              onChange={(e) => updateSub({ tickType: e.target.value })}
                            >
                              <option value="trades">Trade ticks</option>
                              <option value="quotes">Quote BBO</option>
                              <option value="l2">L2 Orderbook</option>
                            </select>
                          )}
                        </div>
                      </td>
                      <td className="py-2.5 px-3 align-middle">
                        <button
                          onClick={() => setSubscriptions((prev) => prev.filter((_, i) => i !== idx))}
                          className="w-6 h-6 rounded-md border border-transparent bg-transparent text-qds-t3 cursor-pointer flex items-center justify-center text-[0.75rem] transition-all hover:border-destructive hover:text-destructive hover:bg-qds-danger-dim"
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="px-3 py-2 border-t border-border flex justify-end">
              <button
                onClick={() =>
                  setSubscriptions((prev) => [
                    ...prev,
                    { exchange: "binance", symbol: "BTCUSDT-PERP", granularity: "bar", dataType: "klines", timeframe: "5m", timeframeValue: 5, timeframeUnit: "m", auto: false },
                  ])
                }
                className="inline-flex items-center font-mono text-[0.62rem] px-2.5 py-1 rounded-md border border-border bg-transparent text-qds-t1 cursor-pointer transition-all hover:border-qds-border-hover hover:text-foreground hover:bg-secondary"
              >
                + 添加订阅
              </button>
            </div>
          </>
        )}
      </div>
      {subscriptions.some((s) => s.granularity === "tick") && (
        <div
          className="flex mt-2.5 px-3 py-2 rounded-md font-mono text-[0.7rem] text-qds-warning items-start gap-2 bg-qds-warning-dim"
          style={{ border: "1px solid color-mix(in srgb, var(--warn) 30%, transparent)" }}
        >
          <span className="text-[0.85rem] leading-none shrink-0">⚠</span>
          <div>
            <div className="font-semibold mb-0.5">数据量提醒</div>
            <div className="font-normal leading-relaxed">
              包含 {subscriptions.filter((s) => s.granularity === "tick").length} 个 tick 数据源。Tick 回测数据量大、运行时间长。建议先用短时间范围验证策略逻辑。
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
