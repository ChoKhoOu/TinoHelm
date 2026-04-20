"use client";

import { useState, useEffect, useRef } from "react";
import { Check } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import {
  FORM_SECTION_CLS,
  FORM_ROW_CLS,
  FORM_ROW_3_CLS,
  FORM_GROUP_CLS,
  FORM_LABEL_CLS,
  FORM_HINT_CLS,
  VIEW_BTN_CLS,
} from "./backtestStyles";
import { BacktestSubscriptionTable, type Subscription } from "./BacktestSubscriptionTable";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface BacktestStrategyInfo {
  name: string;
  type?: string;
}

interface ParamInfo {
  name: string;
  type: string;
  default: string | number | boolean | null;
}

interface CreateForm {
  strategy_name: string;
  start_date: string;
  end_date: string;
  initial_capital: string;
  maker_fee: string;
  taker_fee: string;
  latency_mode: string;
  latency_ms: string;
  fill_model_type: string;
  prob_fill_on_limit: string;
  prob_slippage: string;
  warmup_bars: string;
  tags: string;
}

const FILL_MODEL_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "default", label: "默认 (概率滑点)", hint: "可配置限价单成交概率和滑点概率" },
  { value: "best_price", label: "最优价成交", hint: "始终以最优买卖价成交，无滑点" },
  { value: "one_tick_slippage", label: "固定 1-tick 滑点", hint: "每笔成交固定滑动 1 个最小价格单位" },
  { value: "two_tier", label: "双层深度", hint: "10 手 @ 最优价，其余 @ ±1 tick" },
  { value: "three_tier", label: "三层深度 (50/30/20)", hint: "50 手 @ 最优 / 30 @ ±1 tick / 20 @ ±2 tick" },
  { value: "probabilistic", label: "概率模型 (50/50)", hint: "50% 最优价成交，50% 滑动 ±1 tick" },
  { value: "size_aware", label: "订单量感知", hint: "≤10 手最优价成交，>10 手产生市场冲击" },
  { value: "volume_sensitive", label: "成交量敏感", hint: "可用流动性 = 近期成交量 × 25%，超出部分 ±1 tick" },
  { value: "competition_aware", label: "竞争感知", hint: "模拟多参与者竞争，默认 30% 可见流动性" },
];

function parseTimeframe(tf: string): { value: number; unit: string; clean: string } {
  const m = tf.match(/^(\d+)(s|m|h|d|min|hour)$/i);
  if (!m) return { value: 5, unit: "m", clean: "5m" };
  let unit = m[2].toLowerCase();
  if (unit === "min") unit = "m";
  if (unit === "hour") unit = "h";
  const value = parseInt(m[1]) || 1;
  return { value, unit, clean: `${value}${unit}` };
}

/* ------------------------------------------------------------------ */
/*  BacktestCreateView                                                 */
/* ------------------------------------------------------------------ */

interface BacktestCreateViewProps {
  strategies: BacktestStrategyInfo[];
  onSubmit: () => Promise<void>;
  onCancel: () => void;
}

export function BacktestCreateView({ strategies, onSubmit, onCancel }: BacktestCreateViewProps) {
  const [form, setForm] = useState<CreateForm>({
    strategy_name: "",
    start_date: "2026-01-01",
    end_date: "2026-03-31",
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
  });
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<{ total_bars: number; estimated_label: string } | null>(null);
  const [strategyParams, setStrategyParams] = useState<ParamInfo[]>([]);
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const [paramOverrides, setParamOverrides] = useState<Record<string, string>>({});
  const [strategySearch, setStrategySearch] = useState("");
  const [strategyDropdownOpen, setStrategyDropdownOpen] = useState(false);
  const strategyRef = useRef<HTMLDivElement>(null);
  const sectionsRef = useRef<HTMLDivElement>(null);

  // Animate form sections on mount.
  useEffect(() => {
    const timer = setTimeout(() => {
      sectionsRef.current?.querySelectorAll("[data-form-section]").forEach((el) => el.setAttribute("data-visible", "true"));
    }, 50);
    return () => clearTimeout(timer);
  }, []);

  // Close strategy dropdown on outside click.
  useEffect(() => {
    if (!strategyDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (strategyRef.current && !strategyRef.current.contains(e.target as Node)) {
        setStrategyDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [strategyDropdownOpen]);

  // Fetch estimate when subscriptions or date range change.
  useEffect(() => {
    const symbols = [...new Set(subscriptions.map((s) => s.symbol))];
    const barSubs = subscriptions.filter((s) => s.granularity === "bar");
    const interval = barSubs.length > 0 ? (barSubs[0].timeframe || "5m") : "5m";
    if (symbols.length === 0 || !form.start_date || !form.end_date) { setEstimate(null); return; }
    const timer = setTimeout(() => {
      apiPost<{ total_bars: number; estimated_label: string }>("/api/backtest/estimate", {
        symbols,
        interval,
        start_date: form.start_date,
        end_date: form.end_date,
      }).then((d) => d && setEstimate(d)).catch(() => setEstimate(null));
    }, 300);
    return () => clearTimeout(timer);
  }, [subscriptions, form.start_date, form.end_date]);

  // Fetch strategy params when strategy changes.
  useEffect(() => {
    if (!form.strategy_name) {
      setStrategyParams([]);
      return;
    }
    apiGet<{ name: string; config_params: ParamInfo[]; optimize_ranges?: unknown }>(
      `/api/strategies/${encodeURIComponent(form.strategy_name)}/params`,
    )
      .then((d) => {
        if (d?.config_params) {
          setStrategyParams(d.config_params);
          setParamOverrides({});
          setParamsExpanded(false);
        }
      })
      .catch(() => setStrategyParams([]));
  }, [form.strategy_name]);

  // Fetch strategy defaults and populate subscriptions when strategy changes.
  useEffect(() => {
    if (!form.strategy_name) { setSubscriptions([]); return; }
    apiGet<{
      symbols: string[]; interval: string | null; subscriptions?: Array<{
        exchange: string; symbol: string; granularity: string; timeframe: string | null; tick_type: string | null; auto: boolean;
      }>;
    }>(`/api/strategies/${encodeURIComponent(form.strategy_name)}/defaults`)
      .then((d) => {
        if (d?.subscriptions?.length) {
          setSubscriptions(
            d.subscriptions.map((s) => {
              const parsed = parseTimeframe(s.timeframe || "5m");
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
        } else if (d?.symbols?.length) {
          const parsed = parseTimeframe(d.interval || "5m");
          setSubscriptions(
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
  }, [form.strategy_name]);

  const filteredStrategies = strategies.filter((s) =>
    s.name.toLowerCase().includes(strategySearch.toLowerCase()),
  );

  const submitAction = useAction(
    async () => {
      const capitalNum = parseFloat(form.initial_capital.replace(/,/g, "")) || 100000;
      const params: Record<string, string> = {};
      for (const [k, v] of Object.entries(paramOverrides)) {
        if (v.trim()) params[k] = v.trim();
      }
      const symbols = [...new Set(subscriptions.map((s) => s.symbol))];
      const barSubs = subscriptions.filter((s) => s.granularity === "bar");
      const interval = barSubs.length > 0 ? (barSubs[0].timeframe || "5m") : "5m";
      // Construct fill_model — always include latency_ms (0 when off).
      const fill_model: Record<string, unknown> = {};
      if (form.latency_mode !== "off") {
        fill_model.latency_mode = form.latency_mode;
        fill_model.latency_ms = parseFloat(form.latency_ms) || 5;
      } else {
        fill_model.latency_ms = 0;
      }
      // FillModel type selection (maps to NT built-in models).
      fill_model.fill_model_type = form.fill_model_type;
      if (form.fill_model_type === "default") {
        fill_model.prob_fill_on_limit = parseFloat(form.prob_fill_on_limit) || 1.0;
        fill_model.prob_slippage = parseFloat(form.prob_slippage) || 0.0;
      }
      return apiPost("/api/backtest/run", {
        strategy: form.strategy_name,
        symbols,
        interval,
        start_date: form.start_date,
        end_date: form.end_date,
        initial_capital: capitalNum,
        params: Object.keys(params).length > 0 ? params : undefined,
        data_type: barSubs[0]?.dataType || "klines",
        maker_fee: form.maker_fee || undefined,
        taker_fee: form.taker_fee || undefined,
        fill_model,
        warmup_bars: form.warmup_bars ? parseInt(form.warmup_bars) : undefined,
        tags: form.tags || undefined,
      });
    },
    {
      successDuration: 1200,
      onSuccess: async () => {
        await onSubmit();
        onCancel();
      },
    },
  );

  const handleSubmit = async () => {
    setSubmitError(null);
    if (!form.strategy_name) { setSubmitError("请选择策略"); return; }
    if (subscriptions.length === 0) { setSubmitError("请添加数据订阅"); return; }
    if (!form.start_date || !form.end_date) { setSubmitError("请填写日期范围"); return; }
    await submitAction.execute();
  };

  const typeColors: Record<string, string> = { float: "var(--info)", int: "var(--suc)", bool: "var(--warn)" };

  return (
    <div ref={sectionsRef}>
      {/* Back + Title */}
      <div data-form-section className={`${FORM_SECTION_CLS} flex items-center gap-4 mb-4 pb-4 border-b border-border`}>
        <button className={`${VIEW_BTN_CLS} text-[0.72rem]`} onClick={onCancel}>
          <span className="transition-transform">&larr;</span> 返回
        </button>
        <div>
          <div className="font-mono text-base font-semibold">创建回测</div>
          <div className="text-[0.72rem] text-muted-foreground">配置策略、数据范围和参数，提交后加入队列</div>
        </div>
      </div>

      {/* Section 1: Strategy */}
      <div data-form-section className={FORM_SECTION_CLS} style={strategyDropdownOpen ? { zIndex: 10, position: "relative" } : undefined}>
        <div className="qds-section-label">策略</div>
        <div className={FORM_ROW_CLS}>
          <div className={FORM_GROUP_CLS} ref={strategyRef}>
            <div className={FORM_LABEL_CLS}>策略 <span className="text-destructive text-[0.65rem]">*</span></div>
            <div className="relative">
              <input
                value={strategyDropdownOpen ? strategySearch : form.strategy_name || ""}
                onChange={(e) => { setStrategySearch(e.target.value); setStrategyDropdownOpen(true); }}
                onFocus={() => { setStrategySearch(""); setStrategyDropdownOpen(true); }}
                placeholder="搜索策略..."
                className="qds-input"
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
                        onClick={() => {
                          setForm((f) => ({ ...f, strategy_name: s.name }));
                          setStrategyDropdownOpen(false);
                          setStrategySearch("");
                        }}
                        className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-secondary transition-colors ${
                          form.strategy_name === s.name ? "text-primary" : "text-foreground"
                        }`}
                      >
                        <span className="font-medium">{s.name}</span>
                        {s.type === "portfolio" && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-qds-accent-dim text-qds-info font-medium">组合</span>
                        )}
                        {form.strategy_name === s.name && <Check className="w-3.5 h-3.5 text-primary" />}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
            <div className="text-[0.65rem] text-qds-t3 mt-0.5">选择策略后自动填充数据订阅</div>
          </div>
          <div />
        </div>
      </div>

      {/* Section 2: Data Subscriptions */}
      <BacktestSubscriptionTable
        subscriptions={subscriptions}
        onSubscriptionsChange={setSubscriptions}
      />

      {/* Section 2: Time Range & Capital */}
      <div data-form-section className={FORM_SECTION_CLS}>
        <div className="qds-section-label">时间范围 &amp; 资金</div>
        <div className={FORM_ROW_3_CLS}>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>开始日期 <span className="text-destructive text-[0.65rem]">*</span></div>
            <input className="qds-input" type="date" value={form.start_date} onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
          </div>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>结束日期 <span className="text-destructive text-[0.65rem]">*</span></div>
            <input className="qds-input" type="date" value={form.end_date} onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
          </div>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>初始资金 (USD) <span className="text-destructive text-[0.65rem]">*</span></div>
            <input className="qds-input" type="text" value={form.initial_capital} onChange={(e) => setForm((f) => ({ ...f, initial_capital: e.target.value }))} placeholder="e.g. 100000" />
          </div>
        </div>
      </div>

      {/* Section 2b: Simulation Environment — 3 sub-cards */}
      <div data-form-section className={FORM_SECTION_CLS}>
        <div className="qds-section-label">模拟环境</div>
        <div className="grid grid-cols-3 gap-3">
          {/* Card 1: Fee Model */}
          <div className="bg-card border border-border rounded-lg p-3.5 flex flex-col gap-2.5 transition-colors">
            <div className="font-mono text-[0.6rem] text-muted-foreground uppercase tracking-wider font-semibold">手续费</div>
            <div>
              <div className={FORM_LABEL_CLS}>Maker</div>
              <input className="qds-input" type="text" value={form.maker_fee} onChange={(e) => setForm((f) => ({ ...f, maker_fee: e.target.value }))} placeholder="0.02%" />
            </div>
            <div>
              <div className={FORM_LABEL_CLS}>Taker</div>
              <input className="qds-input" type="text" value={form.taker_fee} onChange={(e) => setForm((f) => ({ ...f, taker_fee: e.target.value }))} placeholder="0.05%" />
            </div>
            <div className="font-mono text-[0.6rem] text-qds-t3 mt-auto">
              MakerTakerFeeModel · 百分比或小数
            </div>
          </div>

          {/* Card 2: Latency Model */}
          <div className="bg-card border border-border rounded-lg p-3.5 flex flex-col gap-2.5 transition-colors">
            <div className="font-mono text-[0.6rem] text-muted-foreground uppercase tracking-wider font-semibold">延迟模拟</div>
            <div>
              <div className={FORM_LABEL_CLS}>模式</div>
              <select className="qds-select" value={form.latency_mode} onChange={(e) => setForm((f) => ({ ...f, latency_mode: e.target.value }))}>
                <option value="off">关闭</option>
                <option value="fixed">固定延迟</option>
              </select>
            </div>
            <div className={form.latency_mode === "off" ? "opacity-35 pointer-events-none" : ""}>
              <div className={FORM_LABEL_CLS}>延迟 (ms)</div>
              <input
                className="qds-input"
                type="text"
                value={form.latency_ms}
                onChange={(e) => setForm((f) => ({ ...f, latency_ms: e.target.value }))}
                placeholder="5"
                disabled={form.latency_mode === "off"}
              />
            </div>
            <div className="font-mono text-[0.6rem] text-qds-t3 mt-auto">
              LatencyModel · 订单到达交易所的网络延迟
            </div>
          </div>

          {/* Card 3: Fill Model */}
          <div className="bg-card border border-border rounded-lg p-3.5 flex flex-col gap-2.5 transition-colors">
            <div className="font-mono text-[0.6rem] text-muted-foreground uppercase tracking-wider font-semibold">成交模型</div>
            <div>
              <div className={FORM_LABEL_CLS}>模型</div>
              <select className="qds-select" value={form.fill_model_type} onChange={(e) => setForm((f) => ({ ...f, fill_model_type: e.target.value }))}>
                {FILL_MODEL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </div>
            {form.fill_model_type === "default" && (
              <>
                <div>
                  <div className={FORM_LABEL_CLS}>限价单成交概率</div>
                  <input className="qds-input" type="text" value={form.prob_fill_on_limit} onChange={(e) => setForm((f) => ({ ...f, prob_fill_on_limit: e.target.value }))} placeholder="1.0" />
                </div>
                <div>
                  <div className={FORM_LABEL_CLS}>滑点概率</div>
                  <input className="qds-input" type="text" value={form.prob_slippage} onChange={(e) => setForm((f) => ({ ...f, prob_slippage: e.target.value }))} placeholder="0.0" />
                </div>
              </>
            )}
            <div className="font-mono text-[0.6rem] text-qds-t3 mt-auto">
              {FILL_MODEL_OPTIONS.find((o) => o.value === form.fill_model_type)?.hint ?? ""}
            </div>
          </div>
        </div>
      </div>

      {/* Section 3: Param Override */}
      <div data-form-section className={FORM_SECTION_CLS}>
        <div className="qds-section-label">
          策略参数覆盖
          <span className="font-normal text-muted-foreground text-[0.55rem] tracking-normal normal-case">· 留空使用默认值</span>
        </div>
        <div className="bg-card border border-border rounded-lg overflow-hidden">
          <div className="flex justify-between items-center px-3.5 py-2.5 border-b border-border font-mono text-[0.72rem] font-semibold">
            <span>参数列表 <span className="text-muted-foreground font-normal">· {strategyParams.length > 0 ? `${strategyParams.length} 个参数` : "选择策略后显示"}</span></span>
            {strategyParams.length > 0 && (
              <button
                className="font-normal text-[0.68rem] text-primary cursor-pointer bg-transparent border-0 transition-opacity hover:opacity-80"
                onClick={() => setParamsExpanded(!paramsExpanded)}
              >
                {paramsExpanded ? "收起 ▴" : "展开全部 ▾"}
              </button>
            )}
          </div>
          <div
            className="overflow-hidden transition-[max-height] duration-[400ms] ease-qds"
            style={{ maxHeight: paramsExpanded ? 600 : 0 }}
          >
            {strategyParams.map((p) => (
              <div key={p.name} className="grid items-center gap-3 px-3.5 py-1.5 border-b border-border last:border-b-0" style={{ gridTemplateColumns: "1fr auto 1fr" }}>
                <div className="font-mono text-[0.72rem] font-medium">
                  {p.name}
                  <span
                    className="text-[0.55rem] ml-1"
                    style={{ color: typeColors[p.type] || "var(--t2)" }}
                  >
                    {p.type}
                  </span>
                </div>
                <div className="font-mono text-[0.68rem] text-qds-t3 text-right">默认: {String(p.default ?? "")}</div>
                <div>
                  <input
                    className="w-full px-2 py-1.5 font-mono text-[0.72rem] bg-input border border-border rounded text-right text-foreground outline-none transition-colors focus:border-primary"
                    placeholder={String(p.default ?? "")}
                    value={paramOverrides[p.name] ?? ""}
                    onChange={(e) => setParamOverrides((prev) => ({ ...prev, [p.name]: e.target.value }))}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Section 4: Advanced */}
      <div data-form-section className={FORM_SECTION_CLS}>
        <div className="qds-section-label">高级选项</div>
        <div className={FORM_ROW_CLS}>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>预热周期 (bars)</div>
            <input className="qds-input" type="text" value={form.warmup_bars} onChange={(e) => setForm((f) => ({ ...f, warmup_bars: e.target.value }))} placeholder="e.g. 200" />
            <div className={FORM_HINT_CLS}>策略初始化需要的最少历史数据</div>
          </div>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>标签</div>
            <input className="qds-input" type="text" value={form.tags} onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))} placeholder="e.g. experiment-01" />
            <div className={FORM_HINT_CLS}>可选，用于标记和筛选</div>
          </div>
        </div>
      </div>

      {/* Submit bar */}
      <div data-form-section className={`${FORM_SECTION_CLS} flex items-center justify-between py-4 border-t border-border mt-6`}>
        <div className="flex flex-col gap-1">
          <div className="font-mono text-[0.72rem] text-muted-foreground">
            预估运行时间 <span className="text-primary">{estimate?.estimated_label ?? "—"}</span>
            {estimate?.total_bars != null && ` · 约 ${(estimate.total_bars / 1_000_000).toFixed(1)}M bars`}
          </div>
          {(submitError || submitAction.error) && (
            <div className="text-[0.72rem] text-destructive">
              {submitError ?? submitAction.error}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <button
            className="inline-flex items-center gap-1 font-mono text-[0.72rem] px-3 py-2 rounded-md border border-border bg-transparent text-qds-t1 cursor-pointer transition-all hover:border-qds-border-hover hover:text-foreground hover:bg-secondary"
            onClick={onCancel}
          >
            取消
          </button>
          <button
            className={`inline-flex items-center gap-1.5 font-mono text-[0.75rem] px-5 py-2 rounded-md border font-medium cursor-pointer transition-all disabled:opacity-50 disabled:pointer-events-none ${
              submitAction.state === "error"
                ? "border-destructive bg-destructive text-white"
                : "border-primary bg-primary text-white hover:opacity-90"
            }`}
            disabled={submitAction.state === "loading" || submitAction.state === "success"}
            onClick={handleSubmit}
          >
            {submitAction.state === "loading" ? "提交中..." : submitAction.state === "success" ? "✓ 已加入队列" : "▶ 提交回测"}
          </button>
        </div>
      </div>
    </div>
  );
}
