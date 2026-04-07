"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Plus, RefreshCw, Check, ChevronDown, Play } from "lucide-react";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { apiGet, apiPost } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import type { RunStatus, TradeLogEntry, BacktestResult } from "./types";
import { OverviewTab } from "./components/OverviewTab";
import { TearsheetTab } from "./components/TearsheetTab";
import { TradeLogTab } from "./components/TradeLogTab";
import { ReportsTab } from "./components/ReportsTab";
import { PerformanceTab } from "./components/PerformanceTab";
import { TradesTab } from "./components/TradesTab";
import { RobustnessTab } from "./components/RobustnessTab";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface BacktestRunSummary {
  run_id: string;
  strategy_name: string;
  symbol: string;
  interval: string;
  start_date: string;
  end_date: string;
  status: RunStatus;
  created_at: string;
  progress_pct?: number | null;
  error?: string | null;
  result_summary?: { total_pnl?: number; total_return_pct?: number; sharpe_ratio?: number } | null;
}

interface StrategyInfo {
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
  exchange: string;
  symbol: string;
  timeframe: string;
  data_type: string;
  start_date: string;
  end_date: string;
  initial_capital: string;
  maker_fee: string;
  taker_fee: string;
  slippage_model: string;
  warmup_bars: string;
  engine_mode: string;
  tags: string;
}

/* ------------------------------------------------------------------ */
/*  Progress ring placeholder for running/queued backtests             */
/* ------------------------------------------------------------------ */

function RunningPlaceholder({ status, pct, fallbackMsg }: { status?: string; pct: number; fallbackMsg?: string }) {
  const isRunning = status === "running" || status === "queued";
  if (!isRunning) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-muted-foreground">
          {status === "failed" ? "回测失败" : (fallbackMsg ?? "回测完成后可查看")}
        </span>
      </div>
    );
  }
  const isQueued = status === "queued";
  const radius = 80;
  const stroke = 6;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (pct / 100) * circumference;

  return (
    <div className="flex items-center justify-center h-full">
      <div className="flex flex-col items-center gap-6 w-80">
        <div className="relative">
          <svg width="184" height="184" viewBox="0 0 184 184">
            <circle cx="92" cy="92" r={radius} fill="none" stroke="var(--bg-t)" strokeWidth={stroke} />
            {!isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--acc)" strokeWidth={stroke}
                strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={offset}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            {isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--info)" strokeWidth={stroke}
                strokeLinecap="round" opacity="0.6"
                strokeDasharray={`${circumference * 0.25} ${circumference * 0.75}`}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", animation: "spin 1.5s linear infinite" }}
              />
            )}
            {!isQueued && pct > 0 && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="var(--acc)" strokeWidth={stroke + 4}
                strokeLinecap="round" opacity="0.35"
                strokeDasharray={circumference} strokeDashoffset={offset}
                filter="url(#arcGlow)"
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            <defs>
              <filter id="arcGlow" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur in="SourceGraphic" stdDeviation="6" />
              </filter>
            </defs>
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            {isQueued ? (
              <span className="text-sm font-medium text-muted-foreground">排队中</span>
            ) : (
              <>
                <span className="text-4xl font-bold font-mono text-foreground" key={pct}>{pct}</span>
                <span className="text-sm font-medium text-muted-foreground -mt-0.5">%</span>
              </>
            )}
          </div>
        </div>
        <span className="text-sm font-medium text-muted-foreground">
          {isQueued ? "等待运行..." : "回测运行中"}
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Copyable ID                                                        */
/* ------------------------------------------------------------------ */

function CopyableId({ runId }: { runId: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation();
    navigator.clipboard.writeText(runId);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <span className="bt-id" title={`点击复制: ${runId}`} onClick={handleCopy}>
      {copied ? (
        <><Check className="w-2.5 h-2.5 text-qds-success" /> copied</>
      ) : (
        runId.slice(0, 8)
      )}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Run Row (for list view)                                            */
/* ------------------------------------------------------------------ */

interface RunRowProps {
  run: BacktestRunSummary;
  progress: number | null;
  expandedId: string | null;
  onToggleExpand: (id: string) => void;
  onViewDetail: (id: string) => void;
}

function RunRow({ run, progress, expandedId, onToggleExpand, onViewDetail }: RunRowProps) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isRunning = run.status === "running";
  const isQueued = run.status === "queued";
  const isDone = run.status === "completed";
  const isFailed = run.status === "failed";
  const isExpandable = isRunning || isQueued;
  const isExpanded = expandedId === run.run_id;
  const pct = progress ?? 0;

  const statusKey = run.status === "completed" ? "done" : run.status === "running" ? "run" : run.status === "failed" ? "fail" : "queue";

  const statusLabel: Record<string, string> = {
    running: "Running",
    completed: "Done",
    failed: "Failed",
    queued: "Queued",
    cancelled: "Cancelled",
    cancelling: "Cancelling",
  };

  const handleRowClick = () => {
    if (isExpandable) {
      onToggleExpand(run.run_id);
    } else {
      onViewDetail(run.run_id);
    }
  };

  return (
    <div className={`bt-row ${isExpanded ? "expanded" : ""}`}>
      <div className="bt-row-main" onClick={handleRowClick}>
        <div className={`bt-accent bt-accent-${statusKey}`} />
        <div className="bt-row-info">
          <div className="bt-row-name">
            {run.strategy_name}
            {(() => {
              const syms = run.symbol.split(",").map((s) => s.trim()).filter(Boolean);
              if (syms.length <= 2) {
                return <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>{syms.join(", ")}</span>;
              }
              return (
                <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>
                  {syms.slice(0, 2).join(", ")}{" "}
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger className="text-muted-foreground" style={{ fontSize: ".65rem", cursor: "default" }}>
                        +{syms.length - 2}
                      </TooltipTrigger>
                      <TooltipContent side="right" className="max-w-xs">
                        <div className="flex flex-wrap gap-1">
                          {syms.map((s) => (
                            <span key={s} className="text-[10px]">{s}</span>
                          ))}
                        </div>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </span>
              );
            })()}
          </div>
          <div className="bt-row-meta">
            <CopyableId runId={run.run_id} />
            {run.interval} · {dateRange}
          </div>
        </div>

        <div className="bt-row-status">
          <span className={`bt-status bt-status-${statusKey}`}>
            {run.status === "running" && <span className="bt-pulse" />}
            {run.status === "completed" && "✓ "}
            {run.status === "failed" && "✕ "}
            {run.status === "queued" && "◦ "}
            {statusLabel[run.status] ?? run.status}
          </span>
        </div>

        <div className="bt-row-right">
          {isRunning && (
            <span className="text-primary">{pct}%</span>
          )}
          {isQueued && (
            <span className="flex items-center gap-[3px] text-qds-t3" style={{ fontFamily: "var(--font-d)", fontSize: ".65rem" }}>
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0s" }} />
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0.2s" }} />
              <span className="inline-block w-1 h-1 rounded-full bg-qds-t3 animate-pulse" style={{ animationDelay: "0.4s" }} />
            </span>
          )}
          {isDone && run.result_summary?.total_pnl != null ? (
            <span className={run.result_summary.total_pnl >= 0 ? "text-qds-success" : "text-destructive"}>
              {run.result_summary.total_pnl >= 0 ? "+" : ""}${Math.abs(run.result_summary.total_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          ) : isDone ? (
            <span className="text-qds-success">Completed</span>
          ) : null}
          {isFailed && <span className="text-destructive" style={{ fontSize: ".68rem" }}>{run.error ? run.error.slice(0, 16) : "Error"}</span>}
        </div>

        <div className="bt-row-action">
          {isExpandable && (
            <span
              className="text-[.68rem] text-[var(--t2)] cursor-pointer"
              style={{
                padding: ".75rem .65rem .75rem 0",
                transition: "transform .3s var(--eo)",
                display: "inline-block",
                transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
              }}
            >
              <ChevronDown className="w-3.5 h-3.5" />
            </span>
          )}
          {(isDone || isFailed) && (
            <button className="bt-view-btn" onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}>
              View <span style={{ transition: "transform 150ms" }}>&rarr;</span>
            </button>
          )}
        </div>
      </div>

      {isRunning && !isExpanded && (
        <div className="bt-progress">
          <div className="bt-progress-fill" style={{ width: `${pct}%` }} />
        </div>
      )}

      {isExpandable && (
        <div className={`bt-expand ${isExpanded ? "open" : ""}`}>
          <div className="bt-expand-inner">
            {isRunning && (
              <>
                <div style={{ height: 6, background: "var(--bg-t)", borderRadius: 3, overflow: "hidden", marginBottom: ".75rem", position: "relative" }}>
                  <div style={{ height: "100%", borderRadius: 3, background: "var(--acc)", transition: "width 1.5s var(--eo)", width: `${pct}%` }} />
                </div>
                <div className="bt-expand-stats">
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Progress</div>
                    <div className="bt-expand-stat-value text-primary">{pct}%</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Status</div>
                    <div className="bt-expand-stat-value text-primary">Running</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Interval</div>
                    <div className="bt-expand-stat-value">{run.interval}</div>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="bt-expand-stat-label">Period</div>
                    <div className="bt-expand-stat-value">{dateRange}</div>
                  </div>
                </div>
              </>
            )}
            {isQueued && (
              <div>
                <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: "1rem" }}>
                  <div>
                    <div style={{ fontSize: ".6rem", color: "var(--t2)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: ".4rem" }}>Preview</div>
                    <div className="rounded-lg bg-secondary" style={{ height: 72 }} />
                  </div>
                  <div>
                    <div style={{ fontSize: ".6rem", color: "var(--t2)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: ".4rem" }}>Config</div>
                    <div className="flex flex-col gap-1.5">
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary" style={{ width: 55, height: 11 }} />
                        <div className="rounded bg-secondary flex-1" style={{ height: 11 }} />
                      </div>
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary" style={{ width: 65, height: 11 }} />
                        <div className="rounded bg-secondary flex-1" style={{ height: 11 }} />
                      </div>
                      <div className="flex gap-1.5">
                        <div className="rounded bg-secondary" style={{ width: 45, height: 11 }} />
                        <div className="rounded bg-secondary flex-1" style={{ height: 11 }} />
                      </div>
                    </div>
                  </div>
                </div>
                <div style={{ marginTop: ".75rem", fontFamily: "var(--font-d)", fontSize: ".7rem", color: "var(--t2)" }}>
                  Estimated start in <span style={{ color: "var(--acc)" }}>~12 min</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  History Row (simplified for completed/failed)                      */
/* ------------------------------------------------------------------ */

function HistoryRow({ run, onViewDetail }: { run: BacktestRunSummary; onViewDetail: (id: string) => void }) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isDone = run.status === "completed";
  const statusKey = isDone ? "done" : "fail";

  return (
    <div className="bt-row">
      <div className="bt-row-main" onClick={() => onViewDetail(run.run_id)}>
        <div className={`bt-accent bt-accent-${statusKey}`} />
        <div className="bt-row-info">
          <div className="bt-row-name">
            {run.strategy_name}
            {(() => {
              const syms = run.symbol.split(",").map((s) => s.trim()).filter(Boolean);
              if (syms.length <= 2) {
                return <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>{syms.join(", ")}</span>;
              }
              return (
                <span style={{ fontWeight: 400, color: "var(--t2)", fontSize: ".7rem" }}>
                  {syms.slice(0, 2).join(", ")} +{syms.length - 2}
                </span>
              );
            })()}
          </div>
          <div className="bt-row-meta">
            <CopyableId runId={run.run_id} />
            {run.interval} · {dateRange}
          </div>
        </div>

        <div className="bt-row-status">
          <span className={`bt-status bt-status-${statusKey}`}>
            {isDone ? "✓ Done" : "✕ Failed"}
          </span>
        </div>

        <div className="bt-row-right">
          {isDone && run.result_summary?.total_pnl != null ? (
            <span className={run.result_summary.total_pnl >= 0 ? "text-qds-success" : "text-destructive"}>
              {run.result_summary.total_pnl >= 0 ? "+" : "-"}${Math.abs(run.result_summary.total_pnl).toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </span>
          ) : isDone ? (
            <span className="text-qds-success">Completed</span>
          ) : (
            <span className="text-destructive" style={{ fontSize: ".68rem" }}>{run.error ? run.error.slice(0, 24) : "Error"}</span>
          )}
        </div>

        <div className="bt-row-action">
          <button className="bt-view-btn" onClick={(e) => { e.stopPropagation(); onViewDetail(run.run_id); }}>
            View <span style={{ transition: "transform 150ms" }}>&rarr;</span>
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Pagination                                                         */
/* ------------------------------------------------------------------ */

function Pager({ curPage, totalPages, total, pageSize, onPageChange }: {
  curPage: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPageChange: (p: number) => void;
}) {
  const start = (curPage - 1) * pageSize + 1;
  const end = Math.min(curPage * pageSize, total);

  const maxBtns = 7;
  let pStart = Math.max(1, curPage - 3);
  let pEnd = Math.min(totalPages, pStart + maxBtns - 1);
  if (pEnd - pStart < maxBtns - 1) pStart = Math.max(1, pEnd - maxBtns + 1);

  const buttons: React.ReactNode[] = [];
  if (pStart > 1) {
    buttons.push(<button key="p1" className="bt-pager-btn" onClick={() => onPageChange(1)}>1</button>);
    if (pStart > 2) buttons.push(<span key="d1" className="bt-pager-dots">...</span>);
  }
  for (let i = pStart; i <= pEnd; i++) {
    buttons.push(
      <button key={i} className={`bt-pager-btn ${i === curPage ? "active" : ""}`} onClick={() => onPageChange(i)}>{i}</button>
    );
  }
  if (pEnd < totalPages) {
    if (pEnd < totalPages - 1) buttons.push(<span key="d2" className="bt-pager-dots">...</span>);
    buttons.push(<button key={`p${totalPages}`} className="bt-pager-btn" onClick={() => onPageChange(totalPages)}>{totalPages}</button>);
  }

  return (
    <div className="bt-pager">
      <span>{start}&ndash;{end} / {total}</span>
      <div className="bt-pager-nav">
        <button className="bt-pager-btn" disabled={curPage <= 1} onClick={() => onPageChange(1)}>&laquo;</button>
        <button className="bt-pager-btn" disabled={curPage <= 1} onClick={() => onPageChange(curPage - 1)}>&lsaquo;</button>
        {buttons}
        <button className="bt-pager-btn" disabled={curPage >= totalPages} onClick={() => onPageChange(curPage + 1)}>&rsaquo;</button>
        <button className="bt-pager-btn" disabled={curPage >= totalPages} onClick={() => onPageChange(totalPages)}>&raquo;</button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Create Form                                                        */
/* ------------------------------------------------------------------ */

function CreateFormView({ strategies, onSubmit, onCancel }: {
  strategies: StrategyInfo[];
  onSubmit: () => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<CreateForm>({
    strategy_name: "",
    exchange: "",
    symbol: "",
    timeframe: "5min",
    data_type: "bars",
    start_date: "2026-01-01",
    end_date: "2026-03-31",
    initial_capital: "100,000",
    maker_fee: "0.02%",
    taker_fee: "0.05%",
    slippage_model: "latency",
    warmup_bars: "200",
    engine_mode: "backtest",
    tags: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [strategyParams, setStrategyParams] = useState<ParamInfo[]>([]);
  const [paramsExpanded, setParamsExpanded] = useState(false);
  const [paramOverrides, setParamOverrides] = useState<Record<string, string>>({});
  const [strategySearch, setStrategySearch] = useState("");
  const [strategyDropdownOpen, setStrategyDropdownOpen] = useState(false);
  const strategyRef = useRef<HTMLDivElement>(null);
  const sectionsRef = useRef<HTMLDivElement>(null);

  // Animate form sections on mount
  useEffect(() => {
    const timer = setTimeout(() => {
      sectionsRef.current?.querySelectorAll(".bt-form-section").forEach((el) => el.classList.add("v"));
    }, 50);
    return () => clearTimeout(timer);
  }, []);

  // Close strategy dropdown on outside click
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

  // Fetch strategy params when strategy changes
  useEffect(() => {
    if (!form.strategy_name) {
      setStrategyParams([]);
      return;
    }
    apiGet<{ name: string; config_params: ParamInfo[]; optimize_ranges?: unknown }>(`/api/strategies/${encodeURIComponent(form.strategy_name)}/params`)
      .then((d) => {
        if (d?.config_params) {
          setStrategyParams(d.config_params);
          setParamOverrides({});
          setParamsExpanded(false);
        }
      })
      .catch(() => setStrategyParams([]));
  }, [form.strategy_name]);

  const filteredStrategies = strategies.filter((s) =>
    s.name.toLowerCase().includes(strategySearch.toLowerCase())
  );

  const handleSubmit = async () => {
    if (!form.strategy_name) { toast.error("请选择策略"); return; }
    if (!form.symbol) { toast.error("请选择品种"); return; }
    if (!form.start_date || !form.end_date) { toast.error("请填写日期范围"); return; }
    setSubmitting(true);
    try {
      const capitalNum = parseFloat(form.initial_capital.replace(/,/g, "")) || 100000;
      const params: Record<string, string> = {};
      for (const [k, v] of Object.entries(paramOverrides)) {
        if (v.trim()) params[k] = v.trim();
      }
      await apiPost("/api/backtest/run", {
        strategy: form.strategy_name,
        symbols: [form.symbol],
        interval: form.timeframe.replace("min", "m").replace("hour", "h"),
        start_date: form.start_date,
        end_date: form.end_date,
        initial_capital: capitalNum,
        params: Object.keys(params).length > 0 ? params : undefined,
      });
      toast.success("回测已提交");
      await onSubmit();
      onCancel();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "提交失败";
      toast.error("提交回测失败", { description: msg });
    } finally {
      setSubmitting(false);
    }
  };

  const typeColors: Record<string, string> = { float: "var(--info)", int: "var(--suc)", bool: "var(--warn)" };

  return (
    <div ref={sectionsRef}>
      {/* Back + Title */}
      <div className="bt-form-section" style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1rem", paddingBottom: "1rem", borderBottom: "1px solid var(--bd)" }}>
        <button className="bt-view-btn" onClick={onCancel} style={{ fontSize: ".72rem", padding: ".35rem .7rem" }}>
          <span style={{ transition: "transform 150ms" }}>&larr;</span> 返回
        </button>
        <div>
          <div style={{ fontFamily: "var(--font-d)", fontSize: "1rem", fontWeight: 600 }}>创建回测</div>
          <div style={{ fontSize: ".72rem", color: "var(--t2)" }}>配置策略、数据范围和参数，提交后加入队列</div>
        </div>
      </div>

      {/* Section 1: Basic Config */}
      <div className="bt-form-section" style={strategyDropdownOpen ? { zIndex: 10, position: "relative" } : undefined}>
        <div className="qds-section-label">基本配置</div>
        <div className="bt-form-row">
          <div className="bt-form-group" ref={strategyRef}>
            <div className="bt-form-label">策略 <span className="req">*</span></div>
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
                        {s.type === "bundle" && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-qds-accent-dim text-qds-info font-medium">组合</span>
                        )}
                        {form.strategy_name === s.name && <Check className="w-3.5 h-3.5 text-primary" />}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">交易所 <span className="req">*</span></div>
            <select className="qds-select" value={form.exchange} onChange={(e) => setForm((f) => ({ ...f, exchange: e.target.value }))}>
              <option value="">选择交易所...</option>
              <option value="binance">Binance Futures</option>
              <option value="hyperliquid">Hyperliquid</option>
              <option value="okx">OKX</option>
            </select>
          </div>
        </div>
        <div className="bt-form-row-3">
          <div className="bt-form-group">
            <div className="bt-form-label">品种 <span className="req">*</span></div>
            <select className="qds-select" value={form.symbol} onChange={(e) => setForm((f) => ({ ...f, symbol: e.target.value }))}>
              <option value="">选择品种...</option>
              <option value="BTCUSDT-PERP">BTCUSDT-PERP</option>
              <option value="ETHUSDT-PERP">ETHUSDT-PERP</option>
              <option value="SOLUSDT-PERP">SOLUSDT-PERP</option>
              <option value="ARBUSDT-PERP">ARBUSDT-PERP</option>
            </select>
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">K线周期 <span className="req">*</span></div>
            <select className="qds-select" value={form.timeframe} onChange={(e) => setForm((f) => ({ ...f, timeframe: e.target.value }))}>
              <option value="1min">1 min</option>
              <option value="5min">5 min</option>
              <option value="15min">15 min</option>
              <option value="1h">1 hour</option>
              <option value="4h">4 hour</option>
            </select>
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">数据类型</div>
            <select className="qds-select" value={form.data_type} onChange={(e) => setForm((f) => ({ ...f, data_type: e.target.value }))}>
              <option value="bars">K线 (Bars)</option>
              <option value="l1">L1 Quotes</option>
              <option value="l2">L2 Orderbook</option>
            </select>
          </div>
        </div>
      </div>

      {/* Section 2: Time Range & Capital */}
      <div className="bt-form-section">
        <div className="qds-section-label">时间范围 &amp; 资金</div>
        <div className="bt-form-row-3">
          <div className="bt-form-group">
            <div className="bt-form-label">开始日期 <span className="req">*</span></div>
            <input className="qds-input" type="date" value={form.start_date} onChange={(e) => setForm((f) => ({ ...f, start_date: e.target.value }))} />
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">结束日期 <span className="req">*</span></div>
            <input className="qds-input" type="date" value={form.end_date} onChange={(e) => setForm((f) => ({ ...f, end_date: e.target.value }))} />
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">初始资金 (USD) <span className="req">*</span></div>
            <input className="qds-input" type="text" value={form.initial_capital} onChange={(e) => setForm((f) => ({ ...f, initial_capital: e.target.value }))} placeholder="e.g. 100000" />
          </div>
        </div>
        <div className="bt-form-row-3">
          <div className="bt-form-group">
            <div className="bt-form-label">Maker 手续费</div>
            <input className="qds-input" type="text" value={form.maker_fee} onChange={(e) => setForm((f) => ({ ...f, maker_fee: e.target.value }))} placeholder="e.g. 0.02%" />
            <div className="bt-form-hint">Binance VIP0 默认 0.02%</div>
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">Taker 手续费</div>
            <input className="qds-input" type="text" value={form.taker_fee} onChange={(e) => setForm((f) => ({ ...f, taker_fee: e.target.value }))} placeholder="e.g. 0.05%" />
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">滑点模型</div>
            <select className="qds-select" value={form.slippage_model} onChange={(e) => setForm((f) => ({ ...f, slippage_model: e.target.value }))}>
              <option value="fixed">固定滑点</option>
              <option value="latency">延迟模拟</option>
              <option value="none">无滑点</option>
            </select>
          </div>
        </div>
      </div>

      {/* Section 3: Param Override */}
      <div className="bt-form-section">
        <div className="qds-section-label">
          策略参数覆盖
          <span style={{ fontWeight: 400, color: "var(--t2)", letterSpacing: 0, textTransform: "none", fontSize: ".55rem" }}>· 留空使用默认值</span>
        </div>
        <div className="bt-po">
          <div className="bt-po-head">
            <span>参数列表 <span style={{ color: "var(--t2)", fontWeight: 400 }}>· {strategyParams.length > 0 ? `${strategyParams.length} 个参数` : "选择策略后显示"}</span></span>
            {strategyParams.length > 0 && (
              <button className="bt-po-toggle" onClick={() => setParamsExpanded(!paramsExpanded)}>
                {paramsExpanded ? "收起 ▴" : "展开全部 ▾"}
              </button>
            )}
          </div>
          <div className={`bt-po-body ${paramsExpanded ? "open" : ""}`}>
            {strategyParams.map((p) => (
              <div key={p.name} className="bt-po-row">
                <div className="bt-po-name">
                  {p.name}
                  <span style={{ fontSize: ".55rem", color: typeColors[p.type] || "var(--t2)", marginLeft: ".2rem" }}>{p.type}</span>
                </div>
                <div className="bt-po-default">默认: {String(p.default ?? "")}</div>
                <div>
                  <input
                    className="bt-po-input"
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
      <div className="bt-form-section">
        <div className="qds-section-label">高级选项</div>
        <div className="bt-form-row-3">
          <div className="bt-form-group">
            <div className="bt-form-label">预热周期 (bars)</div>
            <input className="qds-input" type="text" value={form.warmup_bars} onChange={(e) => setForm((f) => ({ ...f, warmup_bars: e.target.value }))} placeholder="e.g. 200" />
            <div className="bt-form-hint">策略初始化需要的最少历史数据</div>
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">引擎模式</div>
            <select className="qds-select" value={form.engine_mode} onChange={(e) => setForm((f) => ({ ...f, engine_mode: e.target.value }))}>
              <option value="backtest">标准回测</option>
              <option value="sandbox">沙盒模拟</option>
            </select>
          </div>
          <div className="bt-form-group">
            <div className="bt-form-label">标签</div>
            <input className="qds-input" type="text" value={form.tags} onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))} placeholder="e.g. experiment-01" />
            <div className="bt-form-hint">可选，用于标记和筛选</div>
          </div>
        </div>
      </div>

      {/* Submit bar */}
      <div className="bt-submit-bar bt-form-section">
        <div className="bt-submit-est">预估运行时间 <span style={{ color: "var(--acc)" }}>~15 min</span> · 约 1.3M bars</div>
        <div style={{ display: "flex", gap: ".5rem" }}>
          <button className="bt-btn-cancel" onClick={onCancel}>取消</button>
          <button className="bt-btn-submit" disabled={submitting} onClick={handleSubmit}>
            {submitting ? "提交中..." : "▶ 提交回测"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

const DETAIL_TABS = [
  { key: "overview", label: "Overview" },
  { key: "performance", label: "Performance" },
  { key: "trades", label: "Trades" },
  { key: "robustness", label: "Robustness" },
  { key: "tearsheet", label: "Report" },
  { key: "tradelog", label: "Trade Log" },
  { key: "reports", label: "Data Tables" },
];

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [view, setView] = useState<"list" | "create" | "detail">("list");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tradeLog, setTradeLog] = useState<TradeLogEntry[]>([]);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [activeTab, setActiveTab] = useState("overview");
  const [curPage, setCurPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const wsMsg = useWsEvent("backtest.progress");
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});

  const resultCacheRef = useRef<Record<string, BacktestResult>>({});
  const contentRef = useRef<HTMLDivElement>(null);

  // Apply WS progress updates
  useEffect(() => {
    if (!wsMsg) return;
    const raw = (wsMsg.data ?? wsMsg) as Record<string, unknown>;
    const run_id = raw.run_id as string;
    const pct = raw.pct as number;
    if (run_id) {
      setProgressMap((prev) => ({ ...prev, [run_id]: pct }));
      setRuns((prev) =>
        prev.map((r) =>
          r.run_id === run_id && r.status !== "running"
            ? { ...r, status: "running" }
            : r
        )
      );
    }
  }, [wsMsg]);

  // Load runs list
  const loadRuns = useCallback(async () => {
    try {
      const data = await apiGet<{ runs: BacktestRunSummary[]; total: number }>("/api/backtest/runs");
      if (data) setRuns(data.runs ?? []);
    } catch {
      // ignore
    } finally {
      setRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRuns();
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, [loadRuns]);

  // Load strategies
  useEffect(() => {
    apiGet<StrategyInfo[]>("/api/strategies")
      .then((d) => d && setStrategies(d))
      .catch(() => {});
  }, []);

  // Load result when run selected (for trade log)
  useEffect(() => {
    if (!selectedRunId) return;
    const cached = resultCacheRef.current[selectedRunId];
    if (cached) {
      setTradeLog(cached.trade_log ?? []);
      return;
    }
    const run = runs.find((r) => r.run_id === selectedRunId);
    if (run?.status !== "completed") return;

    apiGet<BacktestResult>(`/api/backtest/${selectedRunId}/result`)
      .then((data) => {
        if (data) {
          resultCacheRef.current[selectedRunId] = data;
          setTradeLog(data.trade_log ?? []);
        }
      })
      .catch(() => {});
  }, [selectedRunId, runs]);

  const handleViewDetail = (runId: string) => {
    setSelectedRunId(runId);
    setActiveTab("overview");
    setView("detail");
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleBack = () => {
    setView("list");
    setSelectedRunId(null);
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleToggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  const handleGoCreate = () => {
    setView("create");
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleBackFromCreate = () => {
    setView("list");
    contentRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const selectedRun = runs.find((r) => r.run_id === selectedRunId) ?? null;

  // Split runs into active / history
  const activeRuns = runs.filter((r) => r.status === "running" || r.status === "queued");
  const historyRuns = runs.filter((r) => r.status !== "running" && r.status !== "queued");
  const totalHistoryPages = Math.max(1, Math.ceil(historyRuns.length / pageSize));
  const safePage = Math.min(curPage, totalHistoryPages);
  const historySlice = historyRuns.slice((safePage - 1) * pageSize, safePage * pageSize);

  // Summary counts
  const statusCounts: Record<string, number> = {};
  for (const r of runs) statusCounts[r.status] = (statusCounts[r.status] ?? 0) + 1;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-1 overflow-y-auto" ref={contentRef} style={{ padding: "1.25rem 2rem 4rem" }}>

        {/* ===== LIST VIEW ===== */}
        {view === "list" && (
          <div>
            {/* Header */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "1.5rem" }}>
              <div>
                <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--t0)", marginBottom: ".2rem" }}>回测管理</div>
                <div style={{ fontSize: ".75rem", color: "var(--t2)" }}>
                  {runs.length} 个回测任务
                </div>
              </div>
              <div style={{ display: "flex", gap: ".4rem" }}>
                <button
                  onClick={() => loadRuns()}
                  style={{ fontFamily: "var(--font-d)", fontSize: ".7rem", padding: ".3rem .65rem", borderRadius: "var(--rs)", border: "1px solid var(--bd)", background: "none", color: "var(--t1)", cursor: "pointer", transition: "all 150ms", display: "flex", alignItems: "center", gap: ".3rem" }}
                  title="刷新"
                >
                  <RefreshCw className="w-3 h-3" />
                </button>
                <button
                  onClick={handleGoCreate}
                  style={{ fontFamily: "var(--font-d)", fontSize: ".72rem", padding: ".4rem .85rem", borderRadius: "var(--rs)", border: "1px solid var(--acc)", background: "var(--acc-d)", color: "var(--acc)", cursor: "pointer", transition: "all 150ms", display: "flex", alignItems: "center", gap: ".3rem" }}
                >
                  <Plus className="w-3 h-3" /> 创建回测
                </button>
              </div>
            </div>

            {/* Summary strip */}
            {runs.length > 0 && (
              <div className="bt-summary">
                {(() => {
                  const items: { key: string; color: string; label: string }[] = [];
                  if (statusCounts.running) items.push({ key: "running", color: "var(--info)", label: `${statusCounts.running} Running` });
                  if (statusCounts.completed) items.push({ key: "done", color: "var(--suc)", label: `${statusCounts.completed} Done` });
                  if (statusCounts.failed) items.push({ key: "fail", color: "var(--dan)", label: `${statusCounts.failed} Failed` });
                  if (statusCounts.queued) items.push({ key: "queue", color: "var(--t3)", label: `${statusCounts.queued} Queued` });
                  if (statusCounts.cancelled) items.push({ key: "cancel", color: "var(--t3)", label: `${statusCounts.cancelled} Cancelled` });
                  return items.map((item) => (
                    <div key={item.key} className="bt-summary-item">
                      <div className="bt-summary-dot" style={{ background: item.color }} />
                      <span style={{ color: item.color }}>{item.label}</span>
                    </div>
                  ));
                })()}
              </div>
            )}

            {/* Loading skeleton */}
            {runsLoading ? (
              <div className="bt-list">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="bt-row" style={{ padding: ".75rem 1rem" }}>
                    <Skeleton className="h-10 w-full rounded" />
                  </div>
                ))}
              </div>
            ) : runs.length === 0 ? (
              <div className="bt-list" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: 200 }}>
                <div className="flex flex-col items-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-input flex items-center justify-center">
                    <Play className="w-5 h-5 text-muted-foreground" />
                  </div>
                  <span className="text-xs text-muted-foreground">暂无回测记录</span>
                </div>
              </div>
            ) : (
              <>
                {/* ZONE 1: Active tasks */}
                {activeRuns.length > 0 && (
                  <div className="bt-list">
                    {activeRuns.map((run) => (
                      <RunRow
                        key={run.run_id}
                        run={run}
                        progress={progressMap[run.run_id] ?? run.progress_pct ?? null}
                        expandedId={expandedId}
                        onToggleExpand={handleToggleExpand}
                        onViewDetail={handleViewDetail}
                      />
                    ))}
                  </div>
                )}

                {/* ZONE 2: History */}
                {historyRuns.length > 0 && (
                  <div style={{ marginTop: "1.5rem" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: ".65rem" }}>
                      <div className="qds-section-label" style={{ marginBottom: 0 }}>历史记录</div>
                      <div className="bt-pager-size">
                        <select value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setCurPage(1); }}>
                          <option value={10}>10 条/页</option>
                          <option value={20}>20 条/页</option>
                          <option value={50}>50 条/页</option>
                        </select>
                      </div>
                    </div>
                    <div className="bt-list">
                      {historySlice.map((run) => (
                        <HistoryRow key={run.run_id} run={run} onViewDetail={handleViewDetail} />
                      ))}
                    </div>
                    {totalHistoryPages > 1 && (
                      <Pager
                        curPage={safePage}
                        totalPages={totalHistoryPages}
                        total={historyRuns.length}
                        pageSize={pageSize}
                        onPageChange={(p) => setCurPage(p)}
                      />
                    )}
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* ===== CREATE VIEW ===== */}
        {view === "create" && (
          <CreateFormView
            strategies={strategies}
            onSubmit={async () => { await loadRuns(); }}
            onCancel={handleBackFromCreate}
          />
        )}

        {/* ===== DETAIL VIEW ===== */}
        {view === "detail" && selectedRun && (
          <div>
            {/* Detail top bar */}
            <div style={{ display: "flex", alignItems: "center", gap: "1rem", marginBottom: "1rem", paddingBottom: "1rem", borderBottom: "1px solid var(--bd)" }}>
              <button
                className="bt-view-btn"
                onClick={handleBack}
                style={{ fontSize: ".72rem", padding: ".35rem .7rem" }}
              >
                <span style={{ transition: "transform 150ms" }}>&larr;</span> 返回
              </button>
              <div>
                <div style={{ fontFamily: "var(--font-d)", fontSize: "1rem", fontWeight: 600, display: "flex", alignItems: "center", gap: ".5rem" }}>
                  {selectedRun.strategy_name}
                  <CopyableId runId={selectedRun.run_id} />
                </div>
                <div style={{ fontSize: ".72rem", color: "var(--t2)" }}>
                  {(() => {
                    const syms = selectedRun.symbol.split(",").map((s) => s.trim()).filter(Boolean);
                    return syms.length <= 3 ? syms.join(", ") : `${syms.slice(0, 3).join(", ")} +${syms.length - 3}`;
                  })()} · {selectedRun.interval} · {selectedRun.start_date?.slice(0, 10)} → {selectedRun.end_date?.slice(0, 10)}
                </div>
              </div>
              <div style={{ marginLeft: "auto", display: "flex", gap: ".4rem" }}>
                <button className="bt-act-btn">导出</button>
                <button className="bt-act-btn">克隆</button>
                <button className="bt-act-btn" style={{ color: "var(--dan)" }}>删除</button>
              </div>
            </div>

            {/* Pill tab bar */}
            <div className="bt-tab-bar-wrap" style={{ margin: "0 -2rem", paddingLeft: "2rem", paddingRight: "2rem" }}>
              <div className="bt-tab-bar">
                {DETAIL_TABS.map((tab) => (
                  <button
                    key={tab.key}
                    className={`bt-dtab ${activeTab === tab.key ? "active" : ""}`}
                    onClick={() => setActiveTab(tab.key)}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Tab content */}
            <div className="mt-4">
              {activeTab === "overview" && (
                selectedRun.status === "completed" ? (
                  <OverviewTab runId={selectedRunId!} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} />
                )
              )}
              {activeTab === "performance" && (
                selectedRun.status === "completed" && selectedRunId ? (
                  <PerformanceTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} />
                )
              )}
              {activeTab === "trades" && (
                selectedRun.status === "completed" && selectedRunId ? (
                  <TradesTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} />
                )
              )}
              {activeTab === "robustness" && (
                selectedRun.status === "completed" && selectedRunId ? (
                  <RobustnessTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} />
                )
              )}
              {activeTab === "tearsheet" && (
                selectedRun.status === "completed" ? (
                  <TearsheetTab runId={selectedRunId!} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} />
                )
              )}
              {activeTab === "tradelog" && (
                selectedRun.status === "completed" ? (
                  <TradeLogTab tradeLog={tradeLog} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} />
                )
              )}
              {activeTab === "reports" && (
                selectedRun.status === "completed" ? (
                  <ReportsTab runId={selectedRunId!} />
                ) : (
                  <RunningPlaceholder status={selectedRun.status} pct={progressMap[selectedRunId!] ?? selectedRun.progress_pct ?? 0} />
                )
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
