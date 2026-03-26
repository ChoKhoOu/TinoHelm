"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Search, Plus, Play, RefreshCw, Loader2, Check, Copy, X, CalendarDays } from "lucide-react";
import { format, parse } from "date-fns";
import { toast } from "sonner";
import { zhCN } from "date-fns/locale";
import type { DateRange } from "react-day-picker";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Calendar } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import { apiGet, apiPost } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import { Tooltip, TooltipTrigger, TooltipContent, TooltipProvider } from "@/components/ui/tooltip";
import { StatusBadge } from "@/components/StatusBadge";
import type { RunStatus, TradeLogEntry, BacktestResult } from "./types";
import { OverviewTab } from "./components/OverviewTab";
import { OverviewGreyTab } from "./components/OverviewGreyTab";
import { TearsheetTab } from "./components/TearsheetTab";
import { TradeLogTab } from "./components/TradeLogTab";
import { ReportsTab } from "./components/ReportsTab";
import { PerformanceTab } from "./components/PerformanceTab";

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
        <div className="relative progress-ring-glow">
          <svg width="184" height="184" viewBox="0 0 184 184">
            <circle cx="92" cy="92" r={radius} fill="none" stroke="hsl(var(--muted))" strokeWidth={stroke} />
            {!isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="url(#ringGradient)" strokeWidth={stroke}
                strokeLinecap="round" strokeDasharray={circumference} strokeDashoffset={offset}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            {isQueued && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="hsl(var(--primary))" strokeWidth={stroke}
                strokeLinecap="round" opacity="0.6"
                strokeDasharray={`${circumference * 0.25} ${circumference * 0.75}`}
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", animation: "spin 1.5s linear infinite" }}
              />
            )}
            {!isQueued && pct > 0 && (
              <circle cx="92" cy="92" r={radius} fill="none"
                stroke="url(#ringGradient)" strokeWidth={stroke + 4}
                strokeLinecap="round" opacity="0.35"
                strokeDasharray={circumference} strokeDashoffset={offset}
                filter="url(#arcGlow)"
                style={{ transform: "rotate(-90deg)", transformOrigin: "center", transition: "stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1)" }}
              />
            )}
            <defs>
              <linearGradient id="ringGradient" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#4C9EEB" />
                <stop offset="100%" stopColor="#A78BFA" />
              </linearGradient>
              <filter id="arcGlow" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur in="SourceGraphic" stdDeviation="6" />
              </filter>
            </defs>
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            {isQueued ? (
              <span className="text-sm font-medium text-muted-foreground progress-breathe">排队中</span>
            ) : (
              <>
                <span className="text-4xl font-bold font-heading text-foreground progress-pop" key={pct}>{pct}</span>
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
  error?: string;
}

interface StrategyInfo {
  name: string;
  type?: string; // "single" | "portfolio"
}

interface StrategyDefaults {
  symbols: string[];
  interval: string | null;
  starting_balance: number | null;
}

interface NewRunForm {
  strategy_name: string;
  symbols: string[];
  interval: string;
  start_date: string;
  end_date: string;
  initial_capital: string;
}

/* ------------------------------------------------------------------ */
/*  Status badge                                                       */
/* ------------------------------------------------------------------ */


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
    <span
      className="inline-flex items-center gap-0.5 text-[9px] text-muted-foreground font-mono opacity-60 cursor-pointer hover:text-primary hover:opacity-100 transition-colors"
      title={`点击复制: ${runId}`}
      onClick={handleCopy}
    >
      {runId.slice(0, 8)}
      {copied ? <Check className="w-2.5 h-2.5 text-[var(--accent-green)]" /> : <Copy className="w-2.5 h-2.5" />}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/*  Run Row                                                            */
/* ------------------------------------------------------------------ */

interface RunRowProps {
  run: BacktestRunSummary;
  selected: boolean;
  progress: number | null;
  onClick: () => void;
}

function RunRow({ run, selected, progress, onClick }: RunRowProps) {
  const dateRange = `${run.start_date?.slice(0, 10) ?? "?"} → ${run.end_date?.slice(0, 10) ?? "?"}`;
  const isRunning = run.status === "running" || run.status === "queued";
  const pct = progress ?? 0;
  const createdAt = run.created_at
    ? new Date(run.created_at).toLocaleString("zh-CN", {
        month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
      })
    : null;

  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-3 py-2.5 border-b border-border transition-colors ${
        selected
          ? "bg-[var(--accent-blue-20)] border-l-2 border-l-primary"
          : "hover:bg-muted/60"
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <span className="text-xs font-medium text-foreground truncate flex-1">
          {run.strategy_name}
        </span>
        <StatusBadge status={run.status} />
      </div>
      <div className="flex items-center gap-2 mb-0.5">
        {(() => {
          const syms = run.symbol.split(",").map((s) => s.trim()).filter(Boolean);
          if (syms.length <= 2) {
            return <span className="text-[10px] text-primary font-medium truncate">{syms.join(", ")}</span>;
          }
          return (
            <span className="text-[10px] text-primary font-medium truncate">
              {syms.slice(0, 2).join(", ")}{" "}
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger className="text-[10px] text-primary/70 hover:text-primary cursor-default">
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
        <span className="text-[10px] text-muted-foreground">{run.interval}</span>
      </div>
      <div className="text-[10px] text-muted-foreground mb-0.5">{dateRange}</div>
      <div className="flex items-center justify-between">
        <CopyableId runId={run.run_id} />
        {createdAt && <span className="text-[9px] text-muted-foreground opacity-60">{createdAt}</span>}
      </div>
      {isRunning && (
        <div className="mt-1.5">
          <div className="flex items-center justify-between mb-0.5">
            <div className="flex items-center gap-1">
              <Loader2 className="w-2.5 h-2.5 text-primary animate-spin" />
              <span className="text-[9px] text-primary font-medium">
                {run.status === "queued" ? "排队中" : `${pct}%`}
              </span>
            </div>
          </div>
          <div className="h-1 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500 ease-out relative overflow-hidden"
              style={{ width: `${pct}%` }}
            >
              <div className="progress-shimmer absolute inset-0" />
            </div>
          </div>
        </div>
      )}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  New Run Dialog                                                     */
/* ------------------------------------------------------------------ */

interface NewRunDialogProps {
  open: boolean;
  onClose: () => void;
  strategies: StrategyInfo[];
  onSubmit: (form: NewRunForm) => Promise<void>;
}

function NewRunDialog({ open, onClose, strategies, onSubmit }: NewRunDialogProps) {
  const [form, setForm] = useState<NewRunForm>({
    strategy_name: "",
    symbols: [],
    interval: "5m",
    start_date: "2025-01-01",
    end_date: "2025-03-01",
    initial_capital: "10000",
  });
  const [submitting, setSubmitting] = useState(false);
  const [strategySearch, setStrategySearch] = useState("");
  const [strategyDropdownOpen, setStrategyDropdownOpen] = useState(false);
  const [symbolInput, setSymbolInput] = useState("");
  const [isPortfolio, setIsPortfolio] = useState(false);
  const strategyRef = useRef<HTMLDivElement>(null);
  const [dateRange, setDateRange] = useState<DateRange | undefined>({
    from: parse("2025-01-01", "yyyy-MM-dd", new Date()),
    to: parse("2025-03-01", "yyyy-MM-dd", new Date()),
  });

  // Close dropdown on outside click
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

  // Fetch portfolio defaults when strategy changes
  useEffect(() => {
    if (!form.strategy_name) return;
    const strat = strategies.find((s) => s.name === form.strategy_name);
    const isPf = strat?.type === "portfolio";
    setIsPortfolio(isPf);
    if (!isPf) return;

    apiGet<StrategyDefaults>(`/api/strategies/${encodeURIComponent(form.strategy_name)}/defaults`)
      .then((d) => {
        if (d) {
          setForm((f) => ({
            ...f,
            symbols: d.symbols.length > 0 ? d.symbols : f.symbols,
            interval: d.interval ?? f.interval,
            initial_capital: d.starting_balance ? String(d.starting_balance) : f.initial_capital,
          }));
        }
      })
      .catch(() => {});
  }, [form.strategy_name, strategies]);

  const filteredStrategies = strategies.filter((s) =>
    s.name.toLowerCase().includes(strategySearch.toLowerCase())
  );

  const addSymbol = () => {
    const sym = symbolInput.trim().toUpperCase();
    if (sym && !form.symbols.includes(sym)) {
      setForm((f) => ({ ...f, symbols: [...f.symbols, sym] }));
    }
    setSymbolInput("");
  };

  const removeSymbol = (sym: string) => {
    setForm((f) => ({ ...f, symbols: f.symbols.filter((s) => s !== sym) }));
  };

  const handleSubmit = async () => {
    if (!form.strategy_name) { toast.error("请选择策略"); return; }
    if (!form.start_date || form.start_date.length < 10) { toast.error("请输入完整的起始日期"); return; }
    if (!form.end_date || form.end_date.length < 10) { toast.error("请输入完整的结束日期"); return; }
    setSubmitting(true);
    try {
      await onSubmit(form);
      toast.success("回测已提交");
      onClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "提交失败";
      toast.error("提交回测失败", { description: msg });
    } finally {
      setSubmitting(false);
    }
  };

  const maskDate = (v: string) => {
    const d = v.replace(/\D/g, "").slice(0, 8);
    if (d.length <= 4) return d;
    if (d.length <= 6) return `${d.slice(0, 4)}-${d.slice(4)}`;
    return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}`;
  };

  const labelCls = "text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>新建回测</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-3.5 py-1">
          {/* Strategy searchable selector */}
          <div className="flex flex-col gap-1" ref={strategyRef}>
            <label className={labelCls}>策略</label>
            <div className="relative">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
                <input
                  value={strategyDropdownOpen ? strategySearch : form.strategy_name || ""}
                  onChange={(e) => { setStrategySearch(e.target.value); setStrategyDropdownOpen(true); }}
                  onFocus={() => { setStrategySearch(""); }}
                  placeholder="搜索策略..."
                  className="w-full h-8 pl-8 pr-3 rounded-md border border-input dark:bg-input/30 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring/50 transition-all"
                />
              </div>
              {strategyDropdownOpen && (
                <div className="absolute z-50 top-full left-0 right-0 mt-1 max-h-48 overflow-y-auto rounded-lg border border-border bg-popover shadow-xl animate-in fade-in slide-in-from-top-1 duration-150">
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
                        className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-muted transition-colors ${
                          form.strategy_name === s.name ? "text-primary" : "text-foreground"
                        }`}
                      >
                        <span className="font-medium">{s.name}</span>
                        {s.type === "portfolio" && (
                          <span className="text-[9px] px-1.5 py-0.5 rounded bg-[var(--accent-purple-20)] text-[var(--accent-purple)] font-medium">
                            组合
                          </span>
                        )}
                        {form.strategy_name === s.name && <Check className="w-3.5 h-3.5 text-primary" />}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Symbols — tags with input */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between">
              <label className={labelCls}>交易对</label>
              {isPortfolio && form.symbols.length > 0 && (
                <span className="text-[9px] text-[var(--accent-purple)]">从配置自动填充</span>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5 p-2 min-h-[36px] rounded-md border border-input dark:bg-input/30">
              {form.symbols.map((sym) => (
                <span
                  key={sym}
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-[var(--accent-blue-20)] text-[var(--accent-blue)] text-[10px] font-medium animate-in fade-in zoom-in-95 duration-150"
                >
                  {sym}
                  <button type="button" onClick={() => removeSymbol(sym)} className="hover:text-destructive transition-colors">
                    <X className="w-2.5 h-2.5" />
                  </button>
                </span>
              ))}
              <input
                value={symbolInput}
                onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addSymbol(); }
                  if (e.key === "Backspace" && !symbolInput && form.symbols.length > 0) {
                    removeSymbol(form.symbols[form.symbols.length - 1]);
                  }
                }}
                placeholder={form.symbols.length === 0 ? "输入交易对，回车添加" : ""}
                className="flex-1 min-w-[100px] bg-transparent text-xs text-foreground placeholder:text-muted-foreground focus:outline-none"
              />
            </div>
          </div>

          {/* Interval + Initial capital — same row */}
          <div className="flex gap-3">
            <div className="flex flex-col gap-1 flex-1">
              <label className={labelCls}>时间周期</label>
              <div className="flex gap-2">
                <Input
                  type="number"
                  min={1}
                  value={form.interval.replace(/[^\d]/g, "") || ""}
                  onChange={(e) => {
                    const num = e.target.value;
                    const unit = form.interval.replace(/[\d]/g, "") || "m";
                    setForm((f) => ({ ...f, interval: `${num}${unit}` }));
                  }}
                  placeholder="5"
                  className="flex-1 h-8 text-xs"
                />
                <Select
                  value={form.interval.replace(/[\d]/g, "") || "m"}
                  onValueChange={(unit) => {
                    const num = form.interval.replace(/[^\d]/g, "") || "5";
                    setForm((f) => ({ ...f, interval: `${num}${unit}` }));
                  }}
                >
                  <SelectTrigger className="w-[80px] h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="s">秒 (s)</SelectItem>
                    <SelectItem value="m">分钟 (m)</SelectItem>
                    <SelectItem value="h">小时 (h)</SelectItem>
                    <SelectItem value="d">天 (d)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="flex flex-col gap-1 flex-1">
              <label className={labelCls}>初始资金 (USDT)</label>
              <Input
                type="number"
                value={form.initial_capital}
                onChange={(e) => setForm((f) => ({ ...f, initial_capital: e.target.value }))}
                min="100"
                className="h-8 text-xs"
              />
            </div>
          </div>

          {/* Date range — editable inputs + Popover Calendar */}
          <div className="flex flex-col gap-1">
            <label className={labelCls}>回测区间</label>
            <div className="flex items-center gap-2">
              <Input
                value={form.start_date}
                onChange={(e) => {
                  const v = maskDate(e.target.value);
                  setForm((f) => ({ ...f, start_date: v }));
                  if (v.length === 10) {
                    const d = parse(v, "yyyy-MM-dd", new Date());
                    if (!isNaN(d.getTime())) setDateRange((prev) => ({ from: d, to: prev?.to }));
                  }
                }}
                placeholder="2025-01-01"
                maxLength={10}
                className="flex-1 h-8 text-xs font-mono"
              />
              <span className="text-[10px] text-muted-foreground shrink-0">→</span>
              <Input
                value={form.end_date}
                onChange={(e) => {
                  const v = maskDate(e.target.value);
                  setForm((f) => ({ ...f, end_date: v }));
                  if (v.length === 10) {
                    const d = parse(v, "yyyy-MM-dd", new Date());
                    if (!isNaN(d.getTime())) setDateRange((prev) => ({ from: prev?.from, to: d }));
                  }
                }}
                placeholder="2025-03-01"
                maxLength={10}
                className="flex-1 h-8 text-xs font-mono"
              />
              <Popover>
                <PopoverTrigger
                  className="shrink-0 flex items-center justify-center w-8 h-8 rounded-md border border-input dark:bg-input/30 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors cursor-pointer"
                >
                  <CalendarDays className="w-3.5 h-3.5" />
                </PopoverTrigger>
                <PopoverContent className="w-auto p-3" align="end">
                  <Calendar
                    mode="range"
                    captionLayout="dropdown"
                    numberOfMonths={2}
                    showOutsideDays={false}
                    locale={zhCN}
                    startMonth={new Date(2020, 0)}
                    endMonth={new Date(new Date().getFullYear() + 1, 11)}
                    selected={dateRange}
                    onSelect={() => {}}
                    onDayClick={(day: Date) => {
                      if (!dateRange?.from || (dateRange.from && dateRange.to)) {
                        setDateRange({ from: day, to: undefined });
                        setForm((f) => ({ ...f, start_date: format(day, "yyyy-MM-dd"), end_date: "" }));
                      } else {
                        const [start, end] = day < dateRange.from
                          ? [day, dateRange.from]
                          : [dateRange.from, day];
                        setDateRange({ from: start, to: end });
                        setForm((f) => ({ ...f, start_date: format(start, "yyyy-MM-dd"), end_date: format(end, "yyyy-MM-dd") }));
                      }
                    }}
                    defaultMonth={form.start_date ? parse(form.start_date, "yyyy-MM-dd", new Date()) : undefined}
                  />
                </PopoverContent>
              </Popover>
            </div>
          </div>

        </div>

        <DialogFooter>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
          >
            {submitting ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            {submitting ? "提交中..." : "运行"}
          </button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Page                                                          */
/* ------------------------------------------------------------------ */

export default function BacktestPage() {
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [tradeLog, setTradeLog] = useState<TradeLogEntry[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [activeTab, setActiveTab] = useState("overview");

  // WS progress updates: { run_id, pct }
  const wsMsg = useWsEvent("backtest.progress");
  const [progressMap, setProgressMap] = useState<Record<string, number>>({});

  // Track result fetch to get trade_log
  const resultCacheRef = useRef<Record<string, BacktestResult>>({});

  // Apply WS progress updates
  useEffect(() => {
    if (!wsMsg) return;
    const raw = (wsMsg.data ?? wsMsg) as Record<string, unknown>;
    const run_id = raw.run_id as string;
    const pct = raw.pct as number;
    if (run_id) {
      setProgressMap((prev) => ({ ...prev, [run_id]: pct }));
      // Update status in run list if running
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
    // Poll every 5s while page is mounted to pick up status changes
    const interval = setInterval(loadRuns, 5000);
    return () => clearInterval(interval);
  }, [loadRuns]);

  // Load strategies for new run dialog
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

  // Filter runs
  const filteredRuns = runs.filter((r) => {
    const matchSearch =
      !search ||
      r.strategy_name.toLowerCase().includes(search.toLowerCase()) ||
      r.symbol.toLowerCase().includes(search.toLowerCase());
    const matchStatus = statusFilter === "all" || r.status === statusFilter;
    return matchSearch && matchStatus;
  });

  // Submit new run
  const handleNewRun = async (form: NewRunForm) => {
    await apiPost("/api/backtest/run", {
      strategy: form.strategy_name,
      symbols: form.symbols,
      interval: form.interval,
      start_date: form.start_date,
      end_date: form.end_date,
      initial_capital: parseFloat(form.initial_capital),
    });
    await loadRuns();
  };

  const selectedRun = runs.find((r) => r.run_id === selectedRunId) ?? null;

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── Left panel ── */}
      <div className="w-[380px] shrink-0 flex flex-col border-r border-border bg-card">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div>
            <h1 className="font-heading text-sm font-bold text-foreground">回测</h1>
            <span className="text-[10px] text-muted-foreground">
              {runs.length} 条记录
            </span>
          </div>
          <button
            onClick={() => loadRuns()}
            className="p-1.5 rounded-md text-muted-foreground hover:text-muted-foreground hover:bg-muted transition-colors"
            title="刷新"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Search + Filter */}
        <div className="flex flex-col gap-2 px-3 py-2.5 border-b border-border">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              placeholder="搜索策略或品种..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-7 h-7 text-xs"
            />
          </div>
          <Select value={statusFilter} onValueChange={(v: string | null) => v && setStatusFilter(v)}>
            <SelectTrigger className="h-7 text-xs w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部状态</SelectItem>
              <SelectItem value="queued">排队中</SelectItem>
              <SelectItem value="running">运行中</SelectItem>
              <SelectItem value="completed">已完成</SelectItem>
              <SelectItem value="failed">失败</SelectItem>
              <SelectItem value="cancelled">已取消</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Runs list */}
        <div className="flex-1 overflow-y-auto min-h-0">
          {runsLoading ? (
            <div className="flex flex-col gap-px p-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded" />
              ))}
            </div>
          ) : filteredRuns.length === 0 ? (
            <div className="flex items-center justify-center h-40">
              <span className="text-xs text-muted-foreground">
                {runs.length === 0 ? "暂无回测记录" : "无匹配结果"}
              </span>
            </div>
          ) : (
            filteredRuns.map((run) => (
              <RunRow
                key={run.run_id}
                run={run}
                selected={selectedRunId === run.run_id}
                progress={progressMap[run.run_id] ?? run.progress_pct ?? null}
                onClick={() => {
                  if (selectedRunId !== run.run_id) {
                    setSelectedRunId(run.run_id);
                    setActiveTab("overview-grey");
                  }
                }}
              />
            ))
          )}
        </div>

        {/* New run button */}
        <div className="p-3 border-t border-border">
          <button
            onClick={() => setDialogOpen(true)}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-primary text-primary-foreground text-xs font-semibold hover:opacity-90 transition-opacity"
          >
            <Plus className="w-3.5 h-3.5" />
            新建回测
          </button>
        </div>
      </div>

      {/* ── Right panel ── */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden bg-background">
        {!selectedRunId ? (
          <div className="flex items-center justify-center h-full">
            <div className="flex flex-col items-center gap-3">
              <div className="w-12 h-12 rounded-full bg-popover flex items-center justify-center">
                <Play className="w-5 h-5 text-muted-foreground" />
              </div>
              <span className="text-sm text-muted-foreground">请选择一个回测查看详情</span>
            </div>
          </div>
        ) : (
          <Tabs
            value={activeTab}
            onValueChange={setActiveTab}
            className="flex flex-col h-full"
          >
            {/* Tab header */}
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border bg-card shrink-0">
              <div className="flex flex-col gap-0.5">
                <span className="text-sm font-semibold text-foreground">
                  {selectedRun?.strategy_name}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {(() => {
                    const syms = (selectedRun?.symbol ?? "").split(",").map((s) => s.trim()).filter(Boolean);
                    if (syms.length <= 3) return syms.join(", ");
                    return (
                      <>
                        {syms.slice(0, 3).join(", ")}{" "}
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger className="text-[10px] text-muted-foreground hover:text-foreground cursor-default">
                              +{syms.length - 3}
                            </TooltipTrigger>
                            <TooltipContent side="bottom" className="max-w-sm">
                              <div className="flex flex-wrap gap-1">
                                {syms.map((s) => (
                                  <span key={s} className="text-[10px]">{s}</span>
                                ))}
                              </div>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </>
                    );
                  })()} · {selectedRun?.interval} · {selectedRun?.start_date?.slice(0, 10)} → {selectedRun?.end_date?.slice(0, 10)}
                </span>
              </div>
              {selectedRun && <StatusBadge status={selectedRun.status} />}
            </div>

            <TabsList
              variant="line"
              className="px-4 pt-1 shrink-0 border-b border-border bg-card w-full justify-start rounded-none h-9"
            >
              <TabsTrigger value="overview-grey" className="text-xs px-3">Overview</TabsTrigger>
              <TabsTrigger value="performance" className="text-xs px-3">Performance</TabsTrigger>
              <TabsTrigger value="overview" className="text-xs px-3">概览</TabsTrigger>
              <TabsTrigger value="tearsheet" className="text-xs px-3">报告</TabsTrigger>
              <TabsTrigger value="tradelog" className="text-xs px-3">交易日志</TabsTrigger>
              <TabsTrigger value="reports" className="text-xs px-3">数据表格</TabsTrigger>
            </TabsList>

            <div className="flex-1 min-h-0 overflow-hidden">
              <TabsContent value="overview-grey" className="h-full overflow-y-auto">
                {selectedRun?.status === "completed" ? (
                  <OverviewGreyTab runId={selectedRunId} />
                ) : (
                  <div className="flex items-center justify-center h-full">
                    <span className="text-xs text-muted-foreground">回测完成后可查看</span>
                  </div>
                )}
              </TabsContent>
              <TabsContent value="performance" className="h-full overflow-y-auto">
                {selectedRun?.status === "completed" && selectedRunId ? (
                  <PerformanceTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun?.status} pct={progressMap[selectedRunId!] ?? selectedRun?.progress_pct ?? 0} />
                )}
              </TabsContent>
              <TabsContent value="overview" className="h-full overflow-y-auto">
                {selectedRun?.status === "completed" ? (
                  <OverviewTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder
                    status={selectedRun?.status}
                    pct={progressMap[selectedRunId!] ?? selectedRun?.progress_pct ?? 0}
                    fallbackMsg={selectedRun?.status === "failed" ? `回测失败: ${selectedRun.error ?? "未知错误"}` : "已取消"}
                  />
                )}
              </TabsContent>

              <TabsContent value="tearsheet" className="h-full">
                {selectedRun?.status === "completed" ? (
                  <TearsheetTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun?.status} pct={progressMap[selectedRunId!] ?? selectedRun?.progress_pct ?? 0} />
                )}
              </TabsContent>

              <TabsContent value="tradelog" className="h-full overflow-hidden">
                {selectedRun?.status === "completed" ? (
                  <TradeLogTab tradeLog={tradeLog} />
                ) : (
                  <RunningPlaceholder status={selectedRun?.status} pct={progressMap[selectedRunId!] ?? selectedRun?.progress_pct ?? 0} />
                )}
              </TabsContent>

              <TabsContent value="reports" className="h-full overflow-hidden">
                {selectedRun?.status === "completed" ? (
                  <ReportsTab runId={selectedRunId} />
                ) : (
                  <RunningPlaceholder status={selectedRun?.status} pct={progressMap[selectedRunId!] ?? selectedRun?.progress_pct ?? 0} />
                )}
              </TabsContent>
            </div>
          </Tabs>
        )}
      </div>

      {/* New run dialog */}
      <NewRunDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        strategies={strategies}
        onSubmit={handleNewRun}
      />
    </div>
  );
}
