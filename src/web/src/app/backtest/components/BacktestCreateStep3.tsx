"use client";

import { ChevronDown } from "lucide-react";
import { apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import { InlineError, SectionLabel } from "@/components/qds";
import { BacktestSubscriptionTable, type Subscription } from "./BacktestSubscriptionTable";
import {
  FORM_SECTION_STATIC_CLS,
  FORM_ROW_CLS,
  FORM_GROUP_CLS,
  FORM_LABEL_CLS,
  FORM_HINT_CLS,
} from "./backtestStyles";

/* ------------------------------------------------------------------ */
/*  Fill model options (9 entries)                                      */
/* ------------------------------------------------------------------ */

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

// Assertion: array must have exactly 9 elements
const _assertFillModelCount: 9 = FILL_MODEL_OPTIONS.length as 9;
void _assertFillModelCount;

/* ------------------------------------------------------------------ */
/*  Types                                                               */
/* ------------------------------------------------------------------ */

export interface Step3Form {
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

interface ParamInfo {
  name: string;
  type: string;
  default: string | number | boolean | null;
}

interface BacktestCreateStep3Props {
  // Step 3 form
  form: Step3Form;
  onFormChange: (patch: Partial<Step3Form>) => void;

  // Cross-step data needed for payload construction
  strategy_name: string;
  start_date: string;
  end_date: string;

  // Subscription state (displayed in advanced panel)
  subscriptions: Subscription[];
  onSubscriptionsChange: (next: Subscription[] | ((prev: Subscription[]) => Subscription[])) => void;

  // Strategy params (displayed in advanced panel)
  strategyParams: ParamInfo[];
  paramOverrides: Record<string, string>;
  paramsExpanded: boolean;
  onParamOverridesChange: (overrides: Record<string, string>) => void;
  onParamsExpandedChange: (expanded: boolean) => void;

  // Advanced panel collapse state
  advancedExpanded: boolean;
  onAdvancedExpandedChange: (expanded: boolean) => void;

  // Sheet-level submit callback (called on success, closes sheet + reloads list)
  onSubmit?: () => void;

  // FIX-H7: Sheet open state setter — close sheet after successful submission
  onOpenChange?: (open: boolean) => void;
}

/* ------------------------------------------------------------------ */
/*  BacktestCreateStep3 — 资金 / 费率 / 延迟 + 高级选项 + 提交         */
/* ------------------------------------------------------------------ */

export function BacktestCreateStep3({
  form,
  onFormChange,
  strategy_name,
  start_date,
  end_date,
  subscriptions,
  onSubscriptionsChange,
  strategyParams,
  paramOverrides,
  paramsExpanded,
  onParamOverridesChange,
  onParamsExpandedChange,
  advancedExpanded,
  onAdvancedExpandedChange,
  onSubmit,
  onOpenChange,
}: BacktestCreateStep3Props) {
  const typeColorCls = (type: string) =>
    type === "float" ? "text-qds-info" :
    type === "int" ? "text-qds-success" :
    type === "bool" ? "text-qds-warning" :
    "text-muted-foreground";

  const submitAction = useAction(
    async () => {
      const capitalNum = parseFloat(form.initial_capital.replace(/,/g, "")) || 100000;
      const params: Record<string, string> = {};
      for (const [k, v] of Object.entries(paramOverrides)) {
        if (v.trim()) params[k] = v.trim();
      }
      const barSubs = subscriptions.filter((s) => s.granularity === "bar");
      const interval = barSubs.length > 0 ? (barSubs[0].timeframe || "5m") : "5m";

      // Construct fill_model
      const fill_model: Record<string, unknown> = {};
      if (form.latency_mode !== "off") {
        fill_model.latency_mode = form.latency_mode;
        fill_model.latency_ms = parseFloat(form.latency_ms) || 5;
      } else {
        fill_model.latency_ms = 0;
      }
      fill_model.fill_model_type = form.fill_model_type;
      if (form.fill_model_type === "default") {
        fill_model.prob_fill_on_limit = parseFloat(form.prob_fill_on_limit) || 1.0;
        fill_model.prob_slippage = parseFloat(form.prob_slippage) || 0.0;
      }

      return apiPost("/api/backtest/run", {
        strategy: strategy_name,
        symbols: [...new Set(subscriptions.map((s) => s.symbol))],
        interval,
        start_date,
        end_date,
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
      onSuccess: () => {
        onSubmit?.();
        onOpenChange?.(false);
      },
    },
  );

  return (
    <div className="px-6 py-5 flex flex-col gap-0">

      {/* ── 基础区：资金 ──────────────────────────────────────────── */}
      <div className={FORM_SECTION_STATIC_CLS}>
        <SectionLabel>初始资金</SectionLabel>
        <div className={FORM_ROW_CLS}>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>初始资金 (USDT)</div>
            <div className="relative">
              <input
                className="qds-input pr-14"
                type="text"
                value={form.initial_capital}
                onChange={(e) => onFormChange({ initial_capital: e.target.value })}
                placeholder="100000"
              />
              <span className="absolute right-3 top-1/2 -translate-y-1/2 font-mono text-[0.65rem] text-muted-foreground pointer-events-none">
                USDT
              </span>
            </div>
          </div>
          <div />
        </div>
      </div>

      {/* ── 手续费 ────────────────────────────────────────────────── */}
      <div className={FORM_SECTION_STATIC_CLS}>
        <SectionLabel>手续费</SectionLabel>
        <div className={FORM_ROW_CLS}>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>Maker 费率</div>
            <input
              className="qds-input"
              type="text"
              value={form.maker_fee}
              onChange={(e) => onFormChange({ maker_fee: e.target.value })}
              placeholder="0.02%"
            />
            <div className={FORM_HINT_CLS}>百分比（如 0.02%）或小数（如 0.0002）</div>
          </div>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>Taker 费率</div>
            <input
              className="qds-input"
              type="text"
              value={form.taker_fee}
              onChange={(e) => onFormChange({ taker_fee: e.target.value })}
              placeholder="0.05%"
            />
            <div className={FORM_HINT_CLS}>MakerTakerFeeModel · 百分比或小数</div>
          </div>
        </div>
      </div>

      {/* ── 延迟模拟 ──────────────────────────────────────────────── */}
      <div className={FORM_SECTION_STATIC_CLS}>
        <SectionLabel>延迟模拟</SectionLabel>
        <div className={FORM_ROW_CLS}>
          <div className={FORM_GROUP_CLS}>
            <div className={FORM_LABEL_CLS}>模式</div>
            <select
              className="qds-select"
              value={form.latency_mode}
              onChange={(e) => onFormChange({ latency_mode: e.target.value })}
            >
              <option value="off">关闭</option>
              <option value="fixed">固定延迟</option>
              <option value="realistic">真实延迟</option>
            </select>
          </div>
          <div className={`${FORM_GROUP_CLS} ${form.latency_mode === "off" ? "opacity-35 pointer-events-none" : ""}`}>
            <div className={FORM_LABEL_CLS}>延迟 (ms)</div>
            <input
              className="qds-input"
              type="text"
              value={form.latency_ms}
              onChange={(e) => onFormChange({ latency_ms: e.target.value })}
              placeholder="5"
              disabled={form.latency_mode === "off"}
            />
            <div className={FORM_HINT_CLS}>LatencyModel · 订单到达交易所的网络延迟</div>
          </div>
        </div>
      </div>

      {/* ── 高级选项折叠区 ────────────────────────────────────────── */}
      <div className={FORM_SECTION_STATIC_CLS}>
        <button
          type="button"
          onClick={() => onAdvancedExpandedChange(!advancedExpanded)}
          className="flex items-center gap-1.5 font-mono text-[0.72rem] text-muted-foreground hover:text-foreground transition-colors cursor-pointer mb-3"
        >
          <ChevronDown
            className={`w-4 h-4 transition-transform ${advancedExpanded ? "rotate-180" : ""}`}
          />
          高级选项
        </button>

        {advancedExpanded && (
          <div className="flex flex-col gap-6">

            {/* 成交模型 */}
            <div className="flex flex-col gap-3">
              <SectionLabel>成交模型</SectionLabel>
              <div className={FORM_GROUP_CLS}>
                <div className={FORM_LABEL_CLS}>模型类型</div>
                <select
                  className="qds-select"
                  value={form.fill_model_type}
                  onChange={(e) => onFormChange({ fill_model_type: e.target.value })}
                >
                  {FILL_MODEL_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <div className={FORM_HINT_CLS}>
                  {FILL_MODEL_OPTIONS.find((o) => o.value === form.fill_model_type)?.hint ?? ""}
                </div>
              </div>

              {form.fill_model_type === "default" && (
                <div className={FORM_ROW_CLS}>
                  <div className={FORM_GROUP_CLS}>
                    <div className={FORM_LABEL_CLS}>限价单成交概率</div>
                    <input
                      className="qds-input"
                      type="text"
                      value={form.prob_fill_on_limit}
                      onChange={(e) => onFormChange({ prob_fill_on_limit: e.target.value })}
                      placeholder="1.0"
                    />
                  </div>
                  <div className={FORM_GROUP_CLS}>
                    <div className={FORM_LABEL_CLS}>滑点概率</div>
                    <input
                      className="qds-input"
                      type="text"
                      value={form.prob_slippage}
                      onChange={(e) => onFormChange({ prob_slippage: e.target.value })}
                      placeholder="0.0"
                    />
                  </div>
                </div>
              )}
            </div>

            {/* 数据订阅 */}
            <div>
              <BacktestSubscriptionTable
                subscriptions={subscriptions}
                onSubscriptionsChange={onSubscriptionsChange}
              />
            </div>

            {/* 策略参数覆盖 */}
            <div className="flex flex-col gap-2">
              <SectionLabel>
                策略参数覆盖
                <span className="font-normal text-muted-foreground text-[0.55rem] tracking-normal normal-case">
                  · 留空使用默认值
                </span>
              </SectionLabel>
              <div className="bg-card border border-border rounded-lg overflow-hidden">
                <div className="flex justify-between items-center px-3.5 py-2.5 border-b border-border font-mono text-[0.72rem] font-semibold">
                  <span>
                    参数列表{" "}
                    <span className="text-muted-foreground font-normal">
                      · {strategyParams.length > 0 ? `${strategyParams.length} 个参数` : "选择策略后显示"}
                    </span>
                  </span>
                  {strategyParams.length > 0 && (
                    <button
                      type="button"
                      className="font-normal text-[0.68rem] text-primary cursor-pointer bg-transparent border-0 transition-opacity hover:opacity-80"
                      onClick={() => onParamsExpandedChange(!paramsExpanded)}
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
                    <div
                      key={p.name}
                      className="grid items-center gap-3 px-3.5 py-1.5 border-b border-border last:border-b-0"
                      style={{ gridTemplateColumns: "1fr auto 1fr" }}
                    >
                      <div className="font-mono text-[0.72rem] font-medium">
                        {p.name}
                        <span className={`text-[0.55rem] ml-1 ${typeColorCls(p.type)}`}>
                          {p.type}
                        </span>
                      </div>
                      <div className="font-mono text-[0.68rem] text-qds-t3 text-right">
                        默认: {String(p.default ?? "")}
                      </div>
                      <div>
                        <input
                          className="w-full px-2 py-1.5 font-mono text-[0.72rem] bg-input border border-border rounded text-right text-foreground outline-none transition-colors focus:border-primary"
                          placeholder={String(p.default ?? "")}
                          value={paramOverrides[p.name] ?? ""}
                          onChange={(e) =>
                            onParamOverridesChange({ ...paramOverrides, [p.name]: e.target.value })
                          }
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {/* 预热周期 + 标签 */}
            <div className="flex flex-col gap-3">
              <SectionLabel>其他配置</SectionLabel>
              <div className={FORM_ROW_CLS}>
                <div className={FORM_GROUP_CLS}>
                  <div className={FORM_LABEL_CLS}>预热周期 (bars)</div>
                  <input
                    className="qds-input"
                    type="text"
                    value={form.warmup_bars}
                    onChange={(e) => onFormChange({ warmup_bars: e.target.value })}
                    placeholder="200"
                  />
                  <div className={FORM_HINT_CLS}>策略初始化需要的最少历史数据</div>
                </div>
                <div className={FORM_GROUP_CLS}>
                  <div className={FORM_LABEL_CLS}>标签</div>
                  <input
                    className="qds-input"
                    type="text"
                    value={form.tags}
                    onChange={(e) => onFormChange({ tags: e.target.value })}
                    placeholder="逗号分隔，如 experiment-01,v2"
                  />
                  <div className={FORM_HINT_CLS}>可选，用于标记和筛选</div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── 提交区域 ──────────────────────────────────────────────── */}
      <div className="mt-2 flex flex-col gap-3">
        {/* API worker hint */}
        <p className="font-mono text-[0.65rem] text-muted-foreground">
          任务将在 <strong className="font-semibold text-qds-t1">API 回测 worker</strong> 中执行
        </p>

        {/* Inline error (FR-075) */}
        {submitAction.state === "error" && submitAction.error && (
          <InlineError variant="error">{submitAction.error}</InlineError>
        )}

        {/* Submit button */}
        <button
          type="button"
          className={`inline-flex items-center justify-center gap-1.5 font-mono text-[0.75rem] w-full px-5 py-2.5 rounded-md border font-medium cursor-pointer transition-all disabled:opacity-50 disabled:pointer-events-none ${
            submitAction.state === "error"
              ? "border-destructive bg-destructive text-white"
              : "border-primary bg-primary text-white hover:opacity-90"
          }`}
          disabled={submitAction.state === "loading" || submitAction.state === "success"}
          onClick={() => submitAction.execute()}
        >
          {submitAction.state === "loading"
            ? "提交中..."
            : submitAction.state === "success"
            ? "✓ 已加入队列"
            : "▶ 提交回测"}
        </button>
      </div>
    </div>
  );
}
