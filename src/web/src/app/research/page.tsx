"use client";

import { useState, useEffect, useMemo, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, Legend, ResponsiveContainer, ReferenceLine,
} from "recharts";
import { apiGet, apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import { HelpTip, InlineError } from "@/components/qds";
import { INTERVAL_OPTIONS } from "@/app/data-catalog/types";
import {
  CHART_TOOLTIP_PROPS, CHART_GRID_STYLE, CHART_AXIS_STYLE, CHART_COLORS, CHART_ANIMATION,
} from "@/lib/chartTheme";

/* ================================================================== */
/*  Types                                                              */
/* ================================================================== */

interface SymbolOption {
  symbol: string;
  label?: string;
}

interface FactorDef {
  name: string;
  params?: { key: string; label: string; default: number; unit?: string; tip?: string }[];
}

interface FactorGroup {
  group: string;
  factors: FactorDef[];
}

interface HistoryJob {
  id: string;
  factor: string;
  symbol: string;
  interval: string;
  status: "running" | "completed" | "failed";
  ir: number | null;
  profile: string | null;
  predict: string | null;
  robust: string | null;
  cost: string | null;
  progress: number | null;
  error_msg: string | null;
  created_at: string;
}

interface ExploreFactor {
  name: string;
  ic_mean: number;
  ic_std: number;
  ir: number;
  ic_positive_pct: number;
  rating: string; // "strong" | "usable" | "weak"
}

interface CatalogEntry {
  symbol: string;
  data_type: string;
  interval: string;
  record_count: number | null;
  start_date: string;
  end_date: string;
}

interface ExploreResult {
  factors: ExploreFactor[];
  ic_timeseries: { date: string; [factor: string]: number | string }[];
  ic_decay: { lag: number; ic: number }[];
  quantile_returns: { date: string; Q1: number; Q2: number; Q3: number; Q4: number; Q5: number }[];
  distribution: { bin: string; count: number }[];
  turnover: {
    daily_avg: string;
    annual: string;
    fee_drag: string;
    fee_rate: string;
  };
}

/* ================================================================== */
/*  Fallback data (used when API not available)                        */
/* ================================================================== */

const FALLBACK_SYMBOLS: SymbolOption[] = [
  { symbol: "BTCUSDT-PERP" },
  { symbol: "ETHUSDT-PERP" },
  { symbol: "SOLUSDT-PERP" },
];

const FALLBACK_FACTOR_GROUPS: FactorGroup[] = [
  {
    group: "动量",
    factors: [
      { name: "ret_N", params: [{ key: "lookback", label: "回看周期", default: 20, tip: "计算过去 N 根 bar 的收益率作为因子值" }] },
      { name: "mom_ratio", params: [{ key: "fast", label: "快窗口", default: 5 }, { key: "slow", label: "慢窗口", default: 20 }] },
      { name: "roc", params: [{ key: "period", label: "周期", default: 12 }] },
      { name: "rsi_signal", params: [{ key: "period", label: "RSI 周期", default: 14 }] },
    ],
  },
  {
    group: "波动",
    factors: [
      { name: "vol_ratio", params: [{ key: "fast", label: "快窗口", default: 5, tip: "短期波动率的滚动窗口长度" }, { key: "slow", label: "慢窗口", default: 20, tip: "长期波动率的滚动窗口长度，快/慢比值衡量波动率变化" }] },
      { name: "realized_vol", params: [{ key: "window", label: "窗口", default: 20 }] },
      { name: "atr_norm", params: [{ key: "period", label: "ATR 周期", default: 14 }] },
      { name: "parkinson_vol", params: [{ key: "window", label: "窗口", default: 20 }] },
    ],
  },
  {
    group: "量价",
    factors: [
      { name: "vwap_dev", params: [{ key: "period", label: "周期", default: 20 }] },
      { name: "volume_surge", params: [{ key: "lookback", label: "回看周期", default: 20, tip: "当前成交量 / 过去 N 根 bar 平均成交量" }] },
      { name: "obv_slope", params: [{ key: "period", label: "OBV 斜率周期", default: 10 }] },
    ],
  },
  {
    group: "微观结构",
    factors: [
      { name: "trade_imbalance", params: [{ key: "window", label: "窗口", default: 50 }] },
      { name: "kyle_lambda", params: [{ key: "window", label: "窗口", default: 100 }] },
      { name: "amihud_illiq", params: [{ key: "window", label: "窗口", default: 20 }] },
    ],
  },
  {
    group: "自定义",
    factors: [{ name: "my_momentum", params: [{ key: "lookback", label: "回看周期", default: 10 }] }],
  },
];

/* ================================================================== */
/*  Helpers                                                            */
/* ================================================================== */

const MAX_FACTORS = 5;
const DOT_COLORS = ["var(--suc)", "var(--info)", "var(--acc)", "var(--warn)", "#A882DC"];

function verdictBadge(v: string | null) {
  if (!v || v === "—") return <span className="dim" style={{ fontSize: ".6rem" }}>—</span>;
  const m: Record<string, string> = { pass: "verdict-pass", warn: "verdict-warn", fail: "verdict-fail" };
  return <span className={`verdict ${m[v] ?? ""}`}>{v.toUpperCase()}</span>;
}

function irColor(ir: number | null): string {
  if (ir == null) return "";
  if (ir >= 1) return "cg";
  if (ir >= 0.5) return "";
  return "cr";
}

function timeAgo(dateStr: string): string {
  const d = new Date(dateStr);
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小时前`;
  const days = Math.floor(hrs / 24);
  if (days === 1) return "昨天";
  return `${days} 天前`;
}

/* ================================================================== */
/*  Page component                                                     */
/* ================================================================== */

export default function ResearchPage() {
  /* -- API data ---------------------------------------------------- */
  const [symbols, setSymbols] = useState<SymbolOption[]>(FALLBACK_SYMBOLS);
  const [factorGroups, setFactorGroups] = useState<FactorGroup[]>(FALLBACK_FACTOR_GROUPS);
  const [histJobs, setHistJobs] = useState<HistoryJob[]>([]);
  const [histLoading, setHistLoading] = useState(true);

  /* -- Config state ------------------------------------------------ */
  const [symbol, setSymbol] = useState("BTCUSDT-PERP");
  const [dataType, setDataType] = useState("bar");
  const [interval, setInterval] = useState("5m");
  const [tickSource, setTickSource] = useState<"aggTrades" | "trades">("aggTrades");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-04-01");
  const [selectedFactors, setSelectedFactors] = useState<Set<string>>(new Set(["ret_N", "vol_ratio", "volume_surge"]));

  /* -- Accordion state --------------------------------------------- */
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(["动量"]));
  const toggleGroup = useCallback((g: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      next.has(g) ? next.delete(g) : next.add(g);
      return next;
    });
  }, []);

  /* -- Params state ------------------------------------------------ */
  const [forwardPeriod, setForwardPeriod] = useState(5);
  const [quantiles, setQuantiles] = useState(5);
  const [returnType, setReturnType] = useState("simple");
  const [factorParams, setFactorParams] = useState<Record<string, Record<string, number>>>({});

  /* -- Result state ------------------------------------------------ */
  const [result, setResult] = useState<ExploreResult | null>(null);
  const [hasRun, setHasRun] = useState(false);

  /* -- History pagination ------------------------------------------ */
  const [histPage, setHistPage] = useState(1);
  const histSize = 5;

  /* -- Create factor dialog ----------------------------------------- */
  const [showCreateFactor, setShowCreateFactor] = useState(false);
  const [newFactorName, setNewFactorName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  async function handleCreateFactor() {
    const name = newFactorName.trim();
    if (!name) return;
    setCreating(true);
    setCreateError(null);
    try {
      await apiPost("/api/research/factors/create", { name });
      setShowCreateFactor(false);
      setNewFactorName("");
      // 刷新因子列表
      const groups = await apiGet<FactorGroup[]>("/api/research/factors");
      if (groups && groups.length > 0) setFactorGroups(groups);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setCreateError(msg);
    } finally {
      setCreating(false);
    }
  }

  /* -- Data catalog for availability -------------------------------- */
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);

  const effectiveInterval = dataType === "trade_tick" ? "tick" : interval;

  const dataAvail = useMemo(() => {
    const matches = catalog.filter(
      (c) => c.symbol === symbol && c.data_type === dataType && c.interval === effectiveInterval
    );
    if (matches.length === 0) return null;
    const total = matches.reduce((s, c) => s + (c.record_count ?? 0), 0);
    const minDate = matches.map((c) => c.start_date).sort()[0];
    const maxDate = matches.map((c) => c.end_date).sort().reverse()[0];
    return { total, minDate, maxDate };
  }, [catalog, symbol, dataType, effectiveInterval]);

  /* -- Load initial data ------------------------------------------- */
  useEffect(() => {
    (async () => {
      try {
        const syms = await apiGet<SymbolOption[]>("/api/research/symbols");
        if (syms && syms.length > 0) setSymbols(syms);
      } catch { /* use fallback */ }
      try {
        const groups = await apiGet<FactorGroup[]>("/api/research/factors");
        if (groups && groups.length > 0) setFactorGroups(groups);
      } catch { /* use fallback */ }
      try {
        const cat = await apiGet<CatalogEntry[]>("/api/data/catalog");
        if (cat) setCatalog(cat);
      } catch { /* use fallback */ }
    })();
    loadHistory();
  }, []);

  async function loadHistory() {
    setHistLoading(true);
    try {
      const jobs = await apiGet<HistoryJob[]>("/api/research/jobs");
      if (jobs) setHistJobs(jobs);
    } catch { /* empty */ }
    finally { setHistLoading(false); }
  }

  /* -- Factor selection logic -------------------------------------- */
  const disabledFactors = useMemo(() => {
    if (selectedFactors.size < MAX_FACTORS) return new Set<string>();
    const allNames = factorGroups.flatMap((g) => g.factors.map((f) => f.name));
    return new Set(allNames.filter((n) => !selectedFactors.has(n)));
  }, [selectedFactors, factorGroups]);

  const toggleFactor = useCallback((name: string) => {
    setSelectedFactors((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else if (next.size < MAX_FACTORS) {
        next.add(name);
      }
      return next;
    });
  }, []);

  /* -- Factor param helpers ---------------------------------------- */
  const selectedFactorDefs = useMemo(() => {
    const all = factorGroups.flatMap((g) => g.factors);
    return all.filter((f) => selectedFactors.has(f.name));
  }, [factorGroups, selectedFactors]);

  function getParamValue(factorName: string, key: string, defaultVal: number): number {
    return factorParams[factorName]?.[key] ?? defaultVal;
  }

  function setParamValue(factorName: string, key: string, value: number) {
    setFactorParams((prev) => ({
      ...prev,
      [factorName]: { ...prev[factorName], [key]: value },
    }));
  }

  /* -- Date validation ---------------------------------------------- */
  const dateError = useMemo(() => {
    if (!startDate || !endDate) return "请填写开始和结束日期";
    const s = new Date(startDate);
    const e = new Date(endDate);
    if (isNaN(s.getTime()) || isNaN(e.getTime())) return "日期格式无效";
    if (s >= e) return "结束日期必须晚于开始日期";
    return null;
  }, [startDate, endDate]);

  /* -- Actions ----------------------------------------------------- */
  const explore = useAction(
    () => apiPost<ExploreResult>("/api/research/explore", {
      symbol,
      data_type: dataType,
      interval: effectiveInterval,
      start_date: startDate,
      end_date: endDate,
      factors: Array.from(selectedFactors),
      forward_period: forwardPeriod,
      quantiles,
      return_type: returnType,
      ...(dataType === "trade_tick" ? { tick_source: tickSource } : {}),
      factor_params: Object.fromEntries(
        selectedFactorDefs.map((f) => [
          f.name,
          Object.fromEntries((f.params ?? []).map((p) => [p.key, getParamValue(f.name, p.key, p.default)])),
        ])
      ),
    }),
    {
      onSuccess: (res) => {
        if (res) { setResult(res); setHasRun(true); }
      },
    }
  );

  const diagnose = useAction(
    () => apiPost("/api/research/diagnose", {
      symbol,
      data_type: dataType,
      interval: effectiveInterval,
      start_date: startDate,
      end_date: endDate,
      factors: Array.from(selectedFactors),
      forward_period: forwardPeriod,
      quantiles,
      return_type: returnType,
      ...(dataType === "trade_tick" ? { tick_source: tickSource } : {}),
    }),
    { onSuccess: () => { loadHistory(); } }
  );

  /* -- History pagination ------------------------------------------ */
  const histTotal = histJobs.length;
  const histPages = Math.max(1, Math.ceil(histTotal / histSize));
  const histSafe = Math.min(histPage, histPages);
  const histSlice = histJobs.slice((histSafe - 1) * histSize, histSafe * histSize);

  /* ================================================================ */
  /*  Render                                                           */
  /* ================================================================ */
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>

      {/* -- Page header -- */}
      <div className="page-head" style={{ padding: ".85rem 1.5rem", display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: "1.05rem", fontWeight: 700 }}>Factor Research</div>
          <div className="mono" style={{ fontSize: ".7rem", color: "var(--t2)", marginTop: ".1rem" }}>
            // 探索因子 → 诊断验证 → 部署策略
          </div>
        </div>
      </div>

      {/* -- Explorer: left config + right results -- */}
      <div className="explorer">

        {/* ===== LEFT: Config panel ===== */}
        <div className="config-panel">

          {/* -- 1. Symbol & Data -- */}
          <div className="cfg-section">
            <div className="cfg-title">品种与数据</div>

            <div className="fg">
              <div className="fl">品种</div>
              <select className="fsel" value={symbol} onChange={(e) => setSymbol(e.target.value)}>
                {symbols.map((s) => (
                  <option key={s.symbol} value={s.symbol}>{s.label ?? s.symbol}</option>
                ))}
              </select>
            </div>

            <div className="frow">
              <div className="fg">
                <div className="fl">数据类型</div>
                <select className="fsel" value={dataType} onChange={(e) => setDataType(e.target.value)}>
                  <option value="bar">bar</option>
                  <option value="trade_tick">trade_tick</option>
                </select>
              </div>
              <div className="fg">
                <div className="fl">{dataType === "trade_tick" ? "数据源" : "粒度"}</div>
                {dataType === "trade_tick" ? (
                  <select className="fsel" value={tickSource} onChange={(e) => setTickSource(e.target.value as "aggTrades" | "trades")}>
                    <option value="aggTrades">aggTrades</option>
                    <option value="trades">trades</option>
                  </select>
                ) : (
                  <select className="fsel" value={interval} onChange={(e) => setInterval(e.target.value)}>
                    {INTERVAL_OPTIONS.map((opt) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                )}
              </div>
            </div>

            <div className="frow">
              <div className="fg">
                <div className="fl">开始</div>
                <input className="fi" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
              </div>
              <div className="fg">
                <div className="fl">结束</div>
                <input className="fi" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
              </div>
            </div>

            {dataAvail ? (
              <div className="data-avail">
                ✓ 可用: {dataAvail.total >= 1_000_000
                  ? `${(dataAvail.total / 1_000_000).toFixed(1)}M`
                  : dataAvail.total >= 1_000
                    ? `${(dataAvail.total / 1_000).toFixed(0)}K`
                    : dataAvail.total
                } {dataType === "bar" ? "bars" : dataType === "trade_tick" ? "ticks" : dataType}
                <span style={{ marginLeft: ".4rem", color: "var(--t3)" }}>
                  ({dataAvail.minDate} ~ {dataAvail.maxDate})
                </span>
              </div>
            ) : (
              <div style={{ fontFamily: "var(--font-d)", fontSize: ".62rem", color: "var(--t3)", padding: ".3rem .5rem", background: "var(--bg-t)", borderRadius: "4px", marginTop: ".3rem" }}>
                ✗ 无本地数据
              </div>
            )}
            {dateError && (
              <div style={{ fontFamily: "var(--font-d)", fontSize: ".62rem", color: "var(--dan)", padding: ".3rem .5rem", background: "var(--bg-t)", borderRadius: "4px", marginTop: ".3rem" }}>
                ✗ {dateError}
              </div>
            )}
          </div>

          {/* -- 2. Factor selection -- */}
          <div className="cfg-section">
            <div className="cfg-title">因子选择</div>
            <div className="factor-limit">
              <span className={selectedFactors.size >= MAX_FACTORS ? "lim-full" : "lim-cur"}>
                {selectedFactors.size}
              </span>{" "}
              / {MAX_FACTORS} 已选
            </div>

            {factorGroups.map((g) => {
              const isOpen = openGroups.has(g.group);
              const selCount = g.factors.filter((f) => selectedFactors.has(f.name)).length;
              return (
                <div key={g.group} className={`acc-group${isOpen ? " open" : ""}`}>
                  <div className="acc-head" onClick={() => toggleGroup(g.group)}>
                    <span>
                      {g.group} ({g.factors.length})
                      {selCount > 0 && (
                        <span style={{ marginLeft: ".3rem", fontSize: ".6rem", color: "var(--acc)" }}>{selCount}</span>
                      )}
                    </span>
                    <span className="arr">▸</span>
                  </div>
                  <div className="acc-body">
                    {g.factors.map((f) => {
                      const checked = selectedFactors.has(f.name);
                      const disabled = !checked && disabledFactors.has(f.name);
                      return (
                        <label key={f.name} className={`acc-item${disabled ? " disabled-item" : ""}`}>
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={disabled}
                            onChange={() => toggleFactor(f.name)}
                          />
                          {f.name}
                        </label>
                      );
                    })}
                  </div>
                </div>
              );
            })}
            <div
              style={{ color: "var(--acc)", fontSize: ".65rem", cursor: "pointer", padding: ".45rem .7rem", marginTop: ".3rem" }}
              onClick={() => { setShowCreateFactor(true); setCreateError(null); setNewFactorName(""); }}
            >
              + 新增因子
            </div>
          </div>

          {/* -- 3. Parameters -- */}
          <div className="cfg-section param-section">
            <div className="cfg-title">参数</div>

            {/* Common params */}
            <div className="param-divider"><span className="pdot" /> 通用</div>
            <div className="param-row">
              <span className="param-label">
                预测周期
                <HelpTip text="因子值预测未来 N 根 bar 的收益方向" />
              </span>
              <div className="param-val">
                <input
                  className="param-input"
                  type="number"
                  value={forwardPeriod}
                  onChange={(e) => setForwardPeriod(Number(e.target.value))}
                />
                <span className="param-unit">bars</span>
              </div>
            </div>
            <div className="param-row">
              <span className="param-label">
                分层数量
                <HelpTip text="按因子值从高到低分成 N 组，观察各组收益差异" />
              </span>
              <div className="param-val">
                <input
                  className="param-input"
                  type="number"
                  value={quantiles}
                  onChange={(e) => setQuantiles(Number(e.target.value))}
                />
                <span className="param-unit">组</span>
              </div>
            </div>
            <div className="param-row">
              <span className="param-label">
                收益类型
                <HelpTip text="简单收益 = (P1-P0)/P0，对数收益 = ln(P1/P0)，短周期差异很小" />
              </span>
              <div className="param-val">
                <select
                  className="param-select"
                  value={returnType}
                  onChange={(e) => setReturnType(e.target.value)}
                >
                  <option value="simple">简单收益</option>
                  <option value="log">对数收益</option>
                </select>
              </div>
            </div>

            {/* Per-factor params */}
            {selectedFactorDefs.map((f) => (
              f.params && f.params.length > 0 && (
                <div key={f.name}>
                  <div className="param-divider"><span className="pdot" /> {f.name}</div>
                  {f.params.map((p) => (
                    <div key={p.key} className="param-row">
                      <span className="param-label">
                        {p.label}
                        {p.tip && <HelpTip text={p.tip} />}
                      </span>
                      <div className="param-val">
                        <input
                          className="param-input"
                          type="number"
                          value={getParamValue(f.name, p.key, p.default)}
                          onChange={(e) => setParamValue(f.name, p.key, Number(e.target.value))}
                        />
                        {p.unit && <span className="param-unit">{p.unit}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )
            ))}
          </div>

          {/* -- 4. Actions -- */}
          <div className="action-row">
            <button
              className={`btn ${explore.state === "error" ? "btn-d" : "btn-p"}`}
              style={{ justifyContent: "center" }}
              onClick={explore.execute}
              disabled={explore.state === "loading" || selectedFactors.size === 0 || !!dateError}
            >
              {explore.state === "loading" ? "计算中..." : explore.state === "success" ? "✓ 完成" : "▶ 运行探索"}
            </button>
            {explore.state === "error" && explore.error && <InlineError>{explore.error}</InlineError>}

            <button
              className={`btn ${diagnose.state === "error" ? "btn-d" : diagnose.state === "success" ? "btn-p" : "btn-o"}`}
              style={{ justifyContent: "center" }}
              onClick={diagnose.execute}
              disabled={diagnose.state === "loading" || selectedFactors.size === 0 || !!dateError}
            >
              {diagnose.state === "loading" ? "提交中..." : diagnose.state === "success" ? "✓ 已提交" : "⊕ 提交深度诊断"}
            </button>
            {diagnose.state === "error" && diagnose.error && <InlineError>{diagnose.error}</InlineError>}

            <button
              className="btn btn-g"
              style={{ justifyContent: "center", opacity: 0.4, pointerEvents: "none" }}
            >
              → 部署为策略
            </button>
          </div>
        </div>

        {/* ===== RIGHT: Results ===== */}
        <div className="result-panel">
          {!hasRun && !result ? (
            /* Empty state */
            <div className="empty" style={{ padding: "4rem 2rem" }}>
              <div className="empty-icon" style={{ fontSize: "2rem", marginBottom: "1rem" }}>⧖</div>
              <div className="empty-title">选择品种和因子，开始研究</div>
              <div className="empty-desc">在左侧面板选择品种、勾选因子，然后点击"运行探索"查看结果。</div>
              <a href="/data-catalog" style={{ fontFamily: "var(--font-d)", fontSize: ".72rem", color: "var(--acc)" }}>
                没有数据？去数据目录拉取 →
              </a>
            </div>
          ) : (
            <>
              {/* 1. History reports */}
              <div style={{ marginBottom: "1.5rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".55rem" }}>
                  <div className="sl" style={{ marginBottom: 0 }}>
                    历史诊断报告
                    {histTotal > 0 && (
                      <span style={{ fontWeight: 400, color: "var(--t2)", letterSpacing: 0, textTransform: "none" }}>
                        · {histTotal} 个
                      </span>
                    )}
                  </div>
                  {histPages > 1 && (
                    <div className="hist-pager">
                      <span>{(histSafe - 1) * histSize + 1}–{Math.min(histSafe * histSize, histTotal)} / {histTotal}</span>
                      <button disabled={histSafe <= 1} onClick={() => setHistPage(histSafe - 1)}>‹</button>
                      <button disabled={histSafe >= histPages} onClick={() => setHistPage(histSafe + 1)}>›</button>
                    </div>
                  )}
                </div>

                <div className="cd">
                  <div style={{ padding: 0, overflowX: "auto" }}>
                    <table className="ctbl">
                      <thead>
                        <tr>
                          <th>因子</th>
                          <th>品种</th>
                          <th>状态</th>
                          <th className="tr">IR</th>
                          <th>Profile</th>
                          <th>Predict</th>
                          <th>Robust</th>
                          <th>Cost</th>
                          <th className="tr">时间</th>
                        </tr>
                      </thead>
                      <tbody>
                        {histLoading ? (
                          <tr><td colSpan={9} style={{ textAlign: "center", color: "var(--t3)", padding: "1.5rem" }}>加载中...</td></tr>
                        ) : histSlice.length === 0 ? (
                          <tr><td colSpan={9} style={{ textAlign: "center", color: "var(--t3)", padding: "1.5rem" }}>暂无诊断报告</td></tr>
                        ) : (
                          histSlice.map((job) => (
                            <tr
                              key={job.id}
                              className={job.status === "completed" || job.status === "failed" ? "hist-clickable" : undefined}
                              style={job.status === "failed" ? { opacity: 0.65 } : undefined}
                            >
                              <td>
                                <div style={{ fontWeight: 600 }}>{job.factor}</div>
                                {job.status === "failed" && job.error_msg && (
                                  <div style={{ fontSize: ".58rem", color: "var(--dan)", marginTop: ".1rem" }}>{job.error_msg}</div>
                                )}
                              </td>
                              <td className="dim">{job.symbol} · {job.interval}</td>
                              <td>
                                {job.status === "running" && (
                                  <>
                                    <span className="spinner" />
                                    <span className="ca mono" style={{ fontSize: ".65rem" }}>{job.progress ?? 0}%</span>
                                  </>
                                )}
                                {job.status === "completed" && <span className="verdict verdict-pass">完成</span>}
                                {job.status === "failed" && <span className="verdict verdict-fail">失败</span>}
                              </td>
                              <td className={`tr ${irColor(job.ir)}`} style={{ fontWeight: 600 }}>
                                {job.ir != null ? job.ir.toFixed(2) : "—"}
                              </td>
                              <td>{verdictBadge(job.profile)}</td>
                              <td>{verdictBadge(job.predict)}</td>
                              <td>{verdictBadge(job.robust)}</td>
                              <td>{verdictBadge(job.cost)}</td>
                              <td className="tr dim">{timeAgo(job.created_at)}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              {/* 2. Explore summary table */}
              {result && (
                <>
                  <div className="sl">探索结果 · {result.factors.length} 个因子</div>
                  <div className="cd" style={{ marginBottom: "1.25rem" }}>
                    <div style={{ padding: 0, overflowX: "auto" }}>
                      <table className="ctbl">
                        <thead>
                          <tr>
                            <th>因子</th>
                            <th className="tr">
                              IC̄
                              <HelpTip text="IC 均值，因子预测力的核心指标，>0.03 可用，>0.05 优秀" />
                            </th>
                            <th className="tr">
                              IC Std
                              <HelpTip text="IC 的标准差，越小说明预测力越稳定" />
                            </th>
                            <th className="tr">
                              IR
                              <HelpTip text="信息比率 = IC̄ / IC Std，综合衡量预测力和稳定性，>0.5 可用，>1.0 优秀" />
                            </th>
                            <th className="tr">
                              IC{">"}0%
                              <HelpTip text="IC 为正的期数占比，>55% 说明因子在大多数时间都有效" />
                            </th>
                            <th className="tr">强度</th>
                          </tr>
                        </thead>
                        <tbody>
                          {result.factors.map((f, i) => {
                            const irC = f.ir >= 1 ? "cg" : f.ir >= 0.5 ? "" : "cr dim";
                            return (
                              <tr key={f.name}>
                                <td>
                                  <span className="factor-dot" style={{ background: DOT_COLORS[i % DOT_COLORS.length] }} />
                                  {f.name}
                                </td>
                                <td className="tr" style={{ fontWeight: 600 }}>{f.ic_mean.toFixed(3)}</td>
                                <td className="tr dim">{f.ic_std.toFixed(3)}</td>
                                <td className={`tr ${irC}`} style={{ fontWeight: 600 }}>{f.ir.toFixed(2)}</td>
                                <td className="tr">{f.ic_positive_pct}%</td>
                                <td className="tr">
                                  {f.ir >= 1 && <span className="verdict verdict-pass">强</span>}
                                  {f.ir >= 0.5 && f.ir < 1 && <span className="verdict verdict-warn">可用</span>}
                                  {f.ir < 0.5 && <span className="verdict verdict-fail">弱</span>}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* 3. IC timeseries + IC decay */}
                  <div className="g g2" style={{ marginBottom: "1.25rem" }}>
                    <div className="cd">
                      <div className="cd-h">
                        <span>
                          IC 时序
                          <HelpTip text="每期的 Spearman 秩相关系数，衡量因子排序和未来收益排序的一致性" />
                        </span>
                        <span className="sub">Spearman Rank IC</span>
                      </div>
                      <div className="cd-b" style={{ height: 220 }}>
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={result.ic_timeseries}>
                            <CartesianGrid {...CHART_GRID_STYLE} />
                            <XAxis dataKey="date" {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} />
                            <YAxis {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} tickFormatter={(v: number) => v.toFixed(2)} />
                            <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                            <ReferenceLine y={0} stroke="var(--t3)" strokeDasharray="3 3" strokeOpacity={0.4} />
                            <Legend iconSize={8} wrapperStyle={{ fontSize: ".62rem", fontFamily: "var(--font-d)" }} />
                            {result.factors.map((f, i) => (
                              <Line
                                key={f.name}
                                type="monotone"
                                dataKey={f.name}
                                stroke={DOT_COLORS[i % DOT_COLORS.length]}
                                strokeWidth={1.5}
                                dot={false}
                                animationDuration={CHART_ANIMATION.duration}
                                animationEasing={CHART_ANIMATION.easing}
                              />
                            ))}
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    </div>

                    <div className="cd">
                      <div className="cd-h">
                        <span>
                          IC 衰减
                          <HelpTip text="因子对不同 lag 的预测力，衰减越慢说明信号持续性越好" />
                        </span>
                        <span className="sub">IC Decay</span>
                      </div>
                      <div className="cd-b" style={{ height: 220 }}>
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={result.ic_decay}>
                            <CartesianGrid {...CHART_GRID_STYLE} />
                            <XAxis dataKey="lag" {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} tickFormatter={(v: number) => `lag ${v}`} />
                            <YAxis {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} tickFormatter={(v: number) => v.toFixed(3)} />
                            <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                            <Line
                              type="monotone"
                              dataKey="ic"
                              stroke={CHART_COLORS.success}
                              fill={CHART_COLORS.success}
                              strokeWidth={1.5}
                              dot={{ fill: CHART_COLORS.success, r: 3 }}
                              animationDuration={CHART_ANIMATION.duration}
                              animationEasing={CHART_ANIMATION.easing}
                            />
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    </div>
                  </div>

                  {/* 4. Quantile returns + Distribution */}
                  <div className="g g2" style={{ marginBottom: "1.25rem" }}>
                    <div className="cd">
                      <div className="cd-h">
                        <span>
                          分层累计收益
                          <HelpTip text="按因子值分组后各组的累计收益，Q1(高因子值)和Q5(低因子值)分得越开越好" />
                        </span>
                        <span className="sub">Q1 (高) → Q5 (低)</span>
                      </div>
                      <div className="cd-b" style={{ height: 220 }}>
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={result.quantile_returns}>
                            <CartesianGrid {...CHART_GRID_STYLE} />
                            <XAxis dataKey="date" {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} />
                            <YAxis {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
                            <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                            <Legend iconSize={8} wrapperStyle={{ fontSize: ".62rem", fontFamily: "var(--font-d)" }} />
                            <Line type="monotone" dataKey="Q1" name="Q1 (高)" stroke={CHART_COLORS.success} strokeWidth={1.5} dot={false} />
                            <Line type="monotone" dataKey="Q2" stroke="rgba(54,136,75,0.4)" strokeWidth={1} dot={false} />
                            <Line type="monotone" dataKey="Q3" stroke="var(--t2)" strokeWidth={1} dot={false} strokeDasharray="3 3" />
                            <Line type="monotone" dataKey="Q4" stroke="rgba(254,129,129,0.4)" strokeWidth={1} dot={false} />
                            <Line type="monotone" dataKey="Q5" name="Q5 (低)" stroke={CHART_COLORS.danger} strokeWidth={1.5} dot={false} />
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                    </div>

                    <div className="cd">
                      <div className="cd-h">
                        <span>
                          因子分布
                          <HelpTip text="因子值的频率分布，理想的因子应该接近正态分布，没有极端尖峰或偏斜" />
                        </span>
                        <span className="sub">{result.factors[0]?.name ?? ""}</span>
                      </div>
                      <div className="cd-b" style={{ height: 220 }}>
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={result.distribution}>
                            <CartesianGrid {...CHART_GRID_STYLE} />
                            <XAxis dataKey="bin" {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} />
                            <YAxis {...CHART_AXIS_STYLE} tick={{ ...CHART_AXIS_STYLE }} hide />
                            <RechartsTooltip {...CHART_TOOLTIP_PROPS} />
                            <Bar
                              dataKey="count"
                              fill="var(--info)"
                              fillOpacity={0.4}
                              stroke="var(--info)"
                              strokeWidth={1}
                              radius={[2, 2, 0, 0]}
                              animationDuration={CHART_ANIMATION.duration}
                              animationEasing={CHART_ANIMATION.easing}
                            />
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    </div>
                  </div>

                  {/* 5. Turnover stats */}
                  <div className="turn-row">
                    <div className="turn-item">
                      <div className="turn-label">
                        平均日换手
                        <HelpTip text="每日分层组成员变化比例，换手越高交易成本越大" />
                      </div>
                      <div className="turn-val">{result.turnover.daily_avg}</div>
                    </div>
                    <div className="turn-item">
                      <div className="turn-label">
                        年化换手
                        <HelpTip text="全年的累计换手次数，= 日均换手 × 252" />
                      </div>
                      <div className="turn-val">{result.turnover.annual}</div>
                    </div>
                    <div className="turn-item">
                      <div className="turn-label">
                        隐含手续费损耗
                        <HelpTip text="按当前换手率和费率估算每月被手续费吃掉的收益" />
                      </div>
                      <div className="turn-val cr">{result.turnover.fee_drag}</div>
                    </div>
                    <div className="turn-item">
                      <div className="turn-label">按 {result.turnover.fee_rate} 单边</div>
                      <div className="turn-val dim">taker fee</div>
                    </div>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
      {/* -- Create factor dialog -- */}
      {showCreateFactor && (
        <div
          style={{ position: "fixed", inset: 0, zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.55)" }}
          onClick={() => setShowCreateFactor(false)}
        >
          <div
            style={{ background: "var(--bg-p)", border: "1px solid var(--bd)", borderRadius: 8, padding: "1.2rem 1.5rem", width: 360 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontSize: ".85rem", fontWeight: 600, marginBottom: ".75rem" }}>新增自定义因子</div>
            <div style={{ fontSize: ".65rem", color: "var(--t2)", marginBottom: ".6rem" }}>
              文件将创建在 <span className="mono" style={{ color: "var(--t1)" }}>~/.tino/research/factors/</span> 目录下，基于模板生成
            </div>
            <label style={{ fontSize: ".65rem", color: "var(--t2)", display: "block", marginBottom: ".25rem" }}>因子名称（文件名）</label>
            <input
              className="fi"
              autoFocus
              placeholder="如 my_momentum"
              value={newFactorName}
              onChange={(e) => setNewFactorName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && newFactorName.trim()) handleCreateFactor(); }}
              style={{ width: "100%", marginBottom: ".5rem" }}
            />
            <div style={{ fontSize: ".6rem", color: "var(--t3)", marginBottom: ".6rem" }}>
              仅允许字母、数字、下划线，以字母或下划线开头
            </div>
            {createError && (
              <div style={{ fontSize: ".62rem", color: "var(--dan)", background: "var(--bg-t)", borderRadius: 4, padding: ".3rem .5rem", marginBottom: ".5rem" }}>
                {createError}
              </div>
            )}
            <div style={{ display: "flex", gap: ".5rem", justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setShowCreateFactor(false)}>取消</button>
              <button
                className="btn btn-p"
                disabled={!newFactorName.trim() || creating}
                onClick={handleCreateFactor}
              >
                {creating ? "创建中..." : "创建"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
