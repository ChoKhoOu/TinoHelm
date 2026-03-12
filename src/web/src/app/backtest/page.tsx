"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Play, Loader2, X, ChevronDown, ChevronUp } from "lucide-react";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Input";
import { apiGet, apiPost } from "@/lib/api";
import { useI18n } from "@/i18n";
import {
  LineChart,
  AreaChart,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface EquityCurvePoint {
  timestamp: string;
  equity: number;
  returns_pct: number;
  drawdown_pct: number;
}

interface TradeLogEntry {
  opened_at: string;
  instrument: string;
  side: string;
  quantity: number;
  avg_open: number;
  avg_close: number;
  realized_pnl: number;
  duration: string;
}

interface BacktestStatistics {
  total_pnl: number;
  total_return_pct: number;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  max_drawdown: number | null;
  annual_return: number | null;
  win_rate: number;
  profit_factor: number | null;
  expectancy: number | null;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  largest_win: number | null;
  largest_loss: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  avg_win_loss_ratio: number | null;
  long_pct: number | null;
  short_pct: number | null;
  total_fees: number;
  final_balance: string | null;
  winning_streak: number;
  losing_streak: number;
  avg_holding_time: string | null;
  avg_winning_holding_time: string | null;
  avg_losing_holding_time: string | null;
  gross_profit: number;
  gross_loss: number;
  open_positions: number;
  total_orders: number;
  filled_orders: number;
  returns_volatility: number | null;
}

interface BacktestResult {
  statistics: BacktestStatistics;
  equity_curve: EquityCurvePoint[];
  trade_log: TradeLogEntry[];
}

interface BacktestStatusResponse {
  run_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelling" | "cancelled";
  error?: string;
  progress_pct?: number;
  result?: BacktestResult;
}

interface BacktestRunSummary {
  run_id: string;
  strategy_name: string;
  symbol: string;
  interval: string;
  start_date: string;
  end_date: string;
  status: string;
  created_at: string;
  completed_at?: string;
  result_summary?: Record<string, unknown>;
}

interface OptimizationStatusResponse {
  optimization_id: number;
  status: "running" | "completed" | "failed";
  trials_completed: number;
  total_trials: number;
  best_params?: Record<string, unknown>;
  best_value?: number;
}

interface OptimizationResultResponse {
  optimization_id: number;
  status: string;
  best_params: Record<string, unknown>;
  best_value: number;
  fitness_objective: string;
  all_trials: unknown[];
  train_metrics?: Record<string, number>;
  test_metrics?: Record<string, number>;
}

/* ------------------------------------------------------------------ */
/*  Constants                                                          */
/* ------------------------------------------------------------------ */

const INTERVAL_OPTIONS = [
  { value: "1m", label: "1m" },
  { value: "5m", label: "5m" },
  { value: "15m", label: "15m" },
  { value: "1h", label: "1h" },
  { value: "4h", label: "4h" },
  { value: "1D", label: "1D" },
];

const FITNESS_OPTIONS = [
  { value: "sharpe", label: "Sharpe Ratio" },
  { value: "calmar", label: "Calmar Ratio" },
  { value: "sortino", label: "Sortino Ratio" },
  { value: "profit", label: "Net Profit" },
];

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function fmtNum(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "\u2014";
  return v.toFixed(decimals);
}

function fmtPct(v: number | null | undefined, decimals = 2): string {
  if (v == null) return "\u2014";
  return `${v.toFixed(decimals)}%`;
}

function colorForValue(v: number | null | undefined): string {
  if (v == null) return "text-[var(--text-muted)]";
  if (v > 0) return "text-[var(--accent-green)]";
  if (v < 0) return "text-[var(--accent-red)]";
  return "text-[var(--text-muted)]";
}

/* ------------------------------------------------------------------ */
/*  Page Component                                                     */
/* ------------------------------------------------------------------ */

export default function BacktestPage() {
  const { t } = useI18n();

  // Form state
  const [strategyOptions, setStrategyOptions] = useState<{ value: string; label: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState("");
  const [symbol, setSymbol] = useState("BTCUSDT-PERP.BINANCE");
  const [interval, setInterval_] = useState("1h");
  const [startDate, setStartDate] = useState("2024-01-01");
  const [endDate, setEndDate] = useState("2024-12-31");
  const [initialCapital, setInitialCapital] = useState("10000");
  const [leverage, setLeverage] = useState("1");

  // Backtest run state
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  // Result state
  const [statistics, setStatistics] = useState<BacktestStatistics | null>(null);
  const [equityCurve, setEquityCurve] = useState<EquityCurvePoint[]>([]);
  const [tradeLog, setTradeLog] = useState<TradeLogEntry[]>([]);

  // Optimization state
  const [showOptimize, setShowOptimize] = useState(false);
  const [optNTrials, setOptNTrials] = useState("100");
  const [optFitness, setOptFitness] = useState("sharpe");
  const [optTrainPct, setOptTrainPct] = useState(85);
  const [optimizing, setOptimizing] = useState(false);
  const [optId, setOptId] = useState<number | null>(null);
  const [optTrialsCompleted, setOptTrialsCompleted] = useState(0);
  const [optTotalTrials, setOptTotalTrials] = useState(0);
  const [optBestValue, setOptBestValue] = useState<number | null>(null);
  const [optBestParams, setOptBestParams] = useState<Record<string, unknown> | null>(null);
  const [optResult, setOptResult] = useState<OptimizationResultResponse | null>(null);
  const optPollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  // WebSocket ref for progress events
  const wsRef = useRef<WebSocket | null>(null);

  /* ---------------------------------------------------------------- */
  /*  Load strategies on mount                                         */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    let cancelled = false;
    async function loadStrategies() {
      try {
        const data = await apiGet<{ name: string; file_path: string }[]>("/api/strategies");
        if (cancelled || !data) return;
        setStrategyOptions(data.map((s) => ({ value: s.name, label: s.name })));
        if (data.length > 0) setStrategy(data[0].name);
      } catch {
        setError("backtest.loadStrategiesFailed");
      }
    }
    loadStrategies();
    return () => { cancelled = true; };
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Apply backtest result helper                                     */
  /* ---------------------------------------------------------------- */
  const applyResult = useCallback((result: BacktestResult) => {
    setStatistics(result.statistics);
    setEquityCurve(result.equity_curve ?? []);
    setTradeLog(result.trade_log ?? []);
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Load previous runs on mount                                      */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    let cancelled = false;
    async function loadRuns() {
      try {
        const data = await apiGet<{ runs?: BacktestRunSummary[]; total?: number }>("/api/backtest/runs");
        if (cancelled || !data?.runs?.length) return;
        // If the latest run is completed, load its full result
        const latest = data.runs[0];
        if (latest.status === "completed") {
          const status = await apiGet<BacktestStatusResponse>(`/api/backtest/${latest.run_id}/status`);
          if (cancelled || !status?.result) return;
          applyResult(status.result);
        }
      } catch {
        setError("backtest.loadFailed");
      }
    }
    loadRuns();
    return () => { cancelled = true; };
  }, [applyResult]);

  /* ---------------------------------------------------------------- */
  /*  Cleanup intervals and WS on unmount                              */
  /* ---------------------------------------------------------------- */
  useEffect(() => {
    return () => {
      clearInterval(pollRef.current);
      clearInterval(optPollRef.current);
      wsRef.current?.close();
    };
  }, []);

  /* ---------------------------------------------------------------- */
  /*  Try WebSocket connection for progress                            */
  /* ---------------------------------------------------------------- */
  const tryWebSocket = useCallback((runId: string) => {
    try {
      const wsBase = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/^https:/, "wss:").replace(/^http:/, "ws:");
      const ws = new WebSocket(`${wsBase}/ws/backtest/${runId}`);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.progress_pct != null) setProgress(msg.progress_pct);
          if (msg.status === "completed" && msg.result) {
            applyResult(msg.result);
            setRunning(false);
            setProgress(0);
            setCurrentRunId(null);
            clearInterval(pollRef.current);
            ws.close();
          } else if (msg.status === "failed" || msg.status === "cancelled") {
            setRunning(false);
            setProgress(0);
            setCurrentRunId(null);
            clearInterval(pollRef.current);
            if (msg.status === "cancelled") setError("backtest.cancelled");
            ws.close();
          }
        } catch { /* ignore parse errors */ }
      };

      ws.onerror = () => {
        // Fallback: WebSocket unavailable, HTTP polling will handle it
        ws.close();
        wsRef.current = null;
      };

      ws.onclose = () => {
        wsRef.current = null;
      };
    } catch {
      // WebSocket not available, rely on HTTP polling
    }
  }, [applyResult]);

  /* ---------------------------------------------------------------- */
  /*  Run backtest                                                     */
  /* ---------------------------------------------------------------- */
  const handleRunBacktest = useCallback(async () => {
    if (pollRef.current) clearInterval(pollRef.current);
    wsRef.current?.close();
    setRunning(true);
    setProgress(0);
    setError(null);
    try {
      const result = await apiPost<{ run_id: string }>("/api/backtest/run", {
        strategy,
        symbol,
        interval: interval,
        start_date: startDate,
        end_date: endDate,
        initial_capital: parseFloat(initialCapital) || 10000,
        leverage: parseFloat(leverage) || 1,
      });
      if (!result) return;
      const { run_id } = result;
      setCurrentRunId(run_id);

      // Try WebSocket for real-time progress (optional, with HTTP fallback)
      tryWebSocket(run_id);

      // HTTP polling fallback (always runs, WS may update faster)
      pollRef.current = setInterval(async () => {
        try {
          const status = await apiGet<BacktestStatusResponse>(`/api/backtest/${run_id}/status`);
          if (!status) return;
          if (status.progress_pct != null) setProgress(status.progress_pct);

          if (status.status === "completed" || status.status === "failed" || status.status === "cancelled") {
            clearInterval(pollRef.current);
            wsRef.current?.close();
            setRunning(false);
            setProgress(0);
            setCurrentRunId(null);
            if (status.status === "completed" && status.result) {
              applyResult(status.result);
            }
            if (status.status === "cancelled") {
              setError("backtest.cancelled");
            }
            if (status.status === "failed" && status.error) {
              setError(status.error);
            }
          }
        } catch {
          clearInterval(pollRef.current);
          setRunning(false);
          setProgress(0);
          setCurrentRunId(null);
        }
      }, 2000);
    } catch {
      setRunning(false);
      setProgress(0);
    }
  }, [strategy, symbol, interval, startDate, endDate, initialCapital, leverage, applyResult, tryWebSocket]);

  /* ---------------------------------------------------------------- */
  /*  Cancel backtest                                                  */
  /* ---------------------------------------------------------------- */
  const handleCancel = useCallback(async () => {
    if (!currentRunId) return;
    try {
      await apiPost(`/api/backtest/${currentRunId}/cancel`, {});
    } catch { /* best effort */ }
    clearInterval(pollRef.current);
    wsRef.current?.close();
    setRunning(false);
    setProgress(0);
    setCurrentRunId(null);
  }, [currentRunId]);

  /* ---------------------------------------------------------------- */
  /*  Optimization                                                     */
  /* ---------------------------------------------------------------- */
  const handleStartOptimization = useCallback(async () => {
    if (optPollRef.current) clearInterval(optPollRef.current);
    setOptimizing(true);
    setOptResult(null);
    setOptBestParams(null);
    setOptBestValue(null);
    setOptTrialsCompleted(0);
    setError(null);
    try {
      const res = await apiPost<{ optimization_id: number; status: string }>("/api/backtest/optimize", {
        strategy,
        symbol,
        interval: interval,
        start_date: startDate,
        end_date: endDate,
        initial_capital: parseFloat(initialCapital) || 10000,
        leverage: parseFloat(leverage) || 1,
        n_trials: parseInt(optNTrials) || 100,
        fitness_objective: optFitness,
        train_pct: optTrainPct,
      });
      if (!res) return;
      setOptId(res.optimization_id);
      setOptTotalTrials(parseInt(optNTrials) || 100);

      // Poll optimization status
      optPollRef.current = setInterval(async () => {
        try {
          const status = await apiGet<OptimizationStatusResponse>(`/api/backtest/optimize/${res.optimization_id}/status`);
          if (!status) return;
          setOptTrialsCompleted(status.trials_completed);
          setOptTotalTrials(status.total_trials);
          if (status.best_value != null) setOptBestValue(status.best_value);
          if (status.best_params) setOptBestParams(status.best_params);

          if (status.status === "completed" || status.status === "failed") {
            clearInterval(optPollRef.current);
            setOptimizing(false);
            if (status.status === "completed") {
              // Fetch full result
              try {
                const full = await apiGet<OptimizationResultResponse>(`/api/backtest/optimize/${res.optimization_id}/result`);
                if (full) setOptResult(full);
              } catch { /* use status data */ }
            } else {
              setError("backtest.optimizationFailed");
            }
          }
        } catch {
          clearInterval(optPollRef.current);
          setOptimizing(false);
        }
      }, 3000);
    } catch {
      setOptimizing(false);
    }
  }, [strategy, symbol, interval, startDate, endDate, initialCapital, leverage, optNTrials, optFitness, optTrainPct]);

  const handleRunWithBestParams = useCallback(async () => {
    if (!optBestParams) return;
    if (pollRef.current) clearInterval(pollRef.current);
    setRunning(true);
    setProgress(0);
    setError(null);
    try {
      const result = await apiPost<{ run_id: string }>("/api/backtest/run", {
        strategy,
        symbol,
        interval,
        start_date: startDate,
        end_date: endDate,
        initial_capital: parseFloat(initialCapital) || 10000,
        leverage: parseFloat(leverage) || 1,
        params: optBestParams,
      });
      if (!result) return;
      const { run_id } = result;
      setCurrentRunId(run_id);
      pollRef.current = setInterval(async () => {
        try {
          const status = await apiGet<BacktestStatusResponse>(`/api/backtest/${run_id}/status`);
          if (!status) return;
          if (status.progress_pct != null) setProgress(status.progress_pct);
          if (status.status === "completed" || status.status === "failed" || status.status === "cancelled") {
            clearInterval(pollRef.current);
            setRunning(false);
            setProgress(0);
            if (status.status === "completed" && status.result) {
              setStatistics(status.result.statistics || null);
              setEquityCurve(status.result.equity_curve || []);
              setTradeLog(status.result.trade_log || []);
            }
          }
        } catch {
          clearInterval(pollRef.current);
          setRunning(false);
        }
      }, 2000);
    } catch {
      setRunning(false);
    }
  }, [optBestParams, strategy, symbol, interval, startDate, endDate, initialCapital, leverage]);

  /* ---------------------------------------------------------------- */
  /*  Metric cards definition                                          */
  /* ---------------------------------------------------------------- */
  const metricCards: { label: string; value: string; color: string }[] = statistics
    ? [
        // Row 1
        { label: t("backtest.totalReturn"), value: fmtPct(statistics.total_return_pct), color: colorForValue(statistics.total_return_pct) },
        { label: t("backtest.sharpeRatio"), value: fmtNum(statistics.sharpe_ratio), color: colorForValue(statistics.sharpe_ratio) },
        { label: t("backtest.maxDrawdown"), value: fmtPct(statistics.max_drawdown), color: colorForValue(statistics.max_drawdown != null ? -Math.abs(statistics.max_drawdown) : null) },
        { label: t("backtest.winRate"), value: fmtPct(statistics.win_rate), color: "text-[var(--text-primary)]" },
        { label: t("backtest.profitFactor"), value: fmtNum(statistics.profit_factor), color: colorForValue(statistics.profit_factor != null ? statistics.profit_factor - 1 : null) },
        { label: t("backtest.totalTrades"), value: String(statistics.total_trades), color: "text-[var(--text-primary)]" },
        // Row 2
        { label: t("backtest.sortinoRatio"), value: fmtNum(statistics.sortino_ratio), color: colorForValue(statistics.sortino_ratio) },
        { label: t("backtest.calmarRatio"), value: fmtNum(statistics.calmar_ratio), color: colorForValue(statistics.calmar_ratio) },
        { label: t("backtest.annualReturn"), value: fmtPct(statistics.annual_return), color: colorForValue(statistics.annual_return) },
        { label: t("backtest.expectancy"), value: fmtNum(statistics.expectancy), color: colorForValue(statistics.expectancy) },
        { label: t("backtest.avgWinLoss"), value: fmtNum(statistics.avg_win_loss_ratio), color: colorForValue(statistics.avg_win_loss_ratio != null ? statistics.avg_win_loss_ratio - 1 : null) },
        { label: t("backtest.longShort"), value: `${fmtPct(statistics.long_pct, 0)} / ${fmtPct(statistics.short_pct, 0)}`, color: "text-[var(--text-primary)]" },
      ]
    : Array.from({ length: 12 }, (_, i) => {
        const labels = [
          "backtest.totalReturn", "backtest.sharpeRatio", "backtest.maxDrawdown",
          "backtest.winRate", "backtest.profitFactor", "backtest.totalTrades",
          "backtest.sortinoRatio", "backtest.calmarRatio", "backtest.annualReturn",
          "backtest.expectancy", "backtest.avgWinLoss", "backtest.longShort",
        ];
        return { label: t(labels[i] as Parameters<typeof t>[0]), value: "\u2014", color: "text-[var(--text-muted)]" };
      });

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */
  return (
    <div className="flex flex-col gap-6 p-8">
      {error && (
        <div className="rounded-lg bg-[var(--accent-red-20)] border border-[var(--accent-red)] px-4 py-3 mb-4">
          <span className="font-mono text-[11px] text-[var(--accent-red)]">{t(error as "backtest.loadFailed")}</span>
        </div>
      )}

      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-[-1px] text-[var(--text-primary)]">
            {t("backtest.title")}
          </h1>
          <p className="font-mono text-[12px] text-[var(--text-muted)]">
            {t("backtest.subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {running && (
            <Button
              variant="danger"
              icon={<X className="w-3 h-3" />}
              onClick={handleCancel}
            >
              {t("backtest.cancel")}
            </Button>
          )}
          <Button
            variant="primary"
            icon={running ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            onClick={handleRunBacktest}
            disabled={running || strategyOptions.length === 0}
          >
            {running
              ? `${t("backtest.running")}${progress ? ` ${Math.round(progress)}%` : "..."}`
              : t("backtest.runBacktest")}
          </Button>
        </div>
      </div>

      {/* Config bar */}
      <div className="grid grid-cols-7 gap-4">
        <Select
          label={t("backtest.strategy")}
          value={strategy}
          options={strategyOptions}
          onChange={setStrategy}
        />
        <Input
          label={t("backtest.symbol")}
          placeholder="BTCUSDT-PERP.BINANCE"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
        />
        <Select
          label={t("backtest.interval")}
          value={interval}
          options={INTERVAL_OPTIONS}
          onChange={setInterval_}
        />
        <Input
          label={t("backtest.startDate")}
          type="date"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
        />
        <Input
          label={t("backtest.endDate")}
          type="date"
          value={endDate}
          onChange={(e) => setEndDate(e.target.value)}
        />
        <Input
          label={t("backtest.initialCapital")}
          placeholder="10000"
          type="number"
          value={initialCapital}
          onChange={(e) => setInitialCapital(e.target.value)}
        />
        <Input
          label={t("backtest.leverage")}
          placeholder="1"
          type="number"
          value={leverage}
          onChange={(e) => setLeverage(e.target.value)}
        />
      </div>

      {/* Metrics grid — 2 rows x 6 cols */}
      <div className="grid grid-cols-6 gap-4">
        {metricCards.map((m) => (
          <Card key={m.label} className="p-4">
            <div className="flex flex-col gap-2">
              <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
                {m.label}
              </span>
              <span className={`font-heading text-[24px] font-bold tracking-[-1px] ${m.color}`}>
                {m.value}
              </span>
            </div>
          </Card>
        ))}
      </div>

      {/* Charts + Trade Log */}
      <div className="flex gap-4">
        {/* Left: Equity curve + Drawdown stacked */}
        <Card padding={false} className="flex-1 flex flex-col">
          {/* Equity curve */}
          <div className="flex items-center justify-between px-5 pt-5 pb-3">
            <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              {t("backtest.cumulativeReturns")}
            </span>
          </div>
          <div className="h-px bg-[var(--border-gray)]" />
          <div className="flex-1 px-3 py-4" style={{ minHeight: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={equityCurve}>
                <XAxis
                  dataKey="timestamp"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "JetBrains Mono" }}
                  interval="preserveStartEnd"
                  tickFormatter={(v) => {
                    try { return new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return v; }
                  }}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "JetBrains Mono" }}
                  tickFormatter={(v) => `$${Number(v).toLocaleString()}`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--bg-elevated)",
                    border: "1px solid var(--border-gray)",
                    borderRadius: 8,
                    fontSize: 11,
                    fontFamily: "JetBrains Mono",
                    color: "var(--text-primary)",
                  }}
                  formatter={(value) => [`$${Number(value).toLocaleString()}`, "Equity"]}
                  labelFormatter={(label) => {
                    try { return new Date(label).toLocaleDateString(); } catch { return label; }
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="var(--accent-green)"
                  strokeWidth={2}
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Drawdown area chart */}
          <div className="flex items-center justify-between px-5 pb-2">
            <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              {t("backtest.drawdown")}
            </span>
          </div>
          <div className="px-3 pb-4" style={{ height: 120 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={equityCurve}>
                <XAxis
                  dataKey="timestamp"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "JetBrains Mono" }}
                  interval="preserveStartEnd"
                  tickFormatter={(v) => {
                    try { return new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return v; }
                  }}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 10, fill: "var(--text-muted)", fontFamily: "JetBrains Mono" }}
                  tickFormatter={(v) => `${v}%`}
                  domain={["dataMin", 0]}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--bg-elevated)",
                    border: "1px solid var(--border-gray)",
                    borderRadius: 8,
                    fontSize: 11,
                    fontFamily: "JetBrains Mono",
                    color: "var(--text-primary)",
                  }}
                  formatter={(value) => [`${Number(value).toFixed(2)}%`, "Drawdown"]}
                  labelFormatter={(label) => {
                    try { return new Date(label).toLocaleDateString(); } catch { return label; }
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="drawdown_pct"
                  stroke="var(--accent-red)"
                  fill="var(--accent-red)"
                  fillOpacity={0.2}
                  strokeWidth={1.5}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>

        {/* Right: Trade Log */}
        <Card padding={false} className="w-[520px] flex flex-col">
          <div className="flex items-center justify-between px-5 pt-5 pb-3">
            <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              {t("backtest.tradeLog")}
            </span>
            <span className="text-[10px] font-medium text-[var(--text-muted)]">
              {tradeLog.length} {t("backtest.trades")}
            </span>
          </div>
          <div className="h-px bg-[var(--border-gray)]" />
          {/* Table header */}
          <div className="grid grid-cols-[56px_72px_40px_44px_64px_64px_64px_56px] gap-2 px-5 py-2 border-b border-[var(--border-gray)]">
            {[
              t("common.time"),
              t("common.instrument"),
              t("common.side"),
              t("common.quantity"),
              t("backtest.entryPrice"),
              t("backtest.exitPrice"),
              t("common.pnl"),
              t("backtest.duration"),
            ].map((h) => (
              <span key={h} className="text-[9px] font-bold tracking-[0.5px] text-[var(--text-muted)] uppercase">
                {h}
              </span>
            ))}
          </div>
          {/* Table body */}
          <div className="flex flex-col overflow-auto" style={{ maxHeight: 340 }}>
            {tradeLog.length === 0 && (
              <div className="flex items-center justify-center py-8">
                <span className="text-[11px] text-[var(--text-muted)]">{t("backtest.noData")}</span>
              </div>
            )}
            {tradeLog.map((tr, i) => (
              <div
                key={`${tr.opened_at}-${tr.instrument}-${i}`}
                className={`grid grid-cols-[56px_72px_40px_44px_64px_64px_64px_56px] gap-2 px-5 py-2.5 ${
                  i < tradeLog.length - 1 ? "border-b border-[var(--border-gray)]" : ""
                }`}
              >
                <span className="text-[10px] font-medium text-[var(--text-secondary)] truncate">
                  {(() => { try { return new Date(tr.opened_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return tr.opened_at; } })()}
                </span>
                <span className="text-[10px] font-semibold text-[var(--text-primary)] truncate">{tr.instrument}</span>
                <span
                  className={`text-[10px] font-bold ${
                    tr.side === "BUY" || tr.side === "LONG" ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"
                  }`}
                >
                  {tr.side}
                </span>
                <span className="text-[10px] font-medium text-[var(--text-secondary)]">{fmtNum(tr.quantity, 4)}</span>
                <span className="text-[10px] font-medium text-[var(--text-secondary)]">{fmtNum(tr.avg_open)}</span>
                <span className="text-[10px] font-medium text-[var(--text-secondary)]">{fmtNum(tr.avg_close)}</span>
                <span
                  className={`text-[10px] font-bold ${
                    tr.realized_pnl >= 0 ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"
                  }`}
                >
                  {tr.realized_pnl >= 0 ? "+" : ""}{fmtNum(tr.realized_pnl)}
                </span>
                <span className="text-[10px] font-medium text-[var(--text-secondary)] truncate">{tr.duration}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Optimization Section */}
      <Card padding={false}>
        <button
          type="button"
          className="flex items-center justify-between w-full px-5 py-4 text-left"
          onClick={() => setShowOptimize(!showOptimize)}
        >
          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
            {t("backtest.optimization")}
          </span>
          {showOptimize ? (
            <ChevronUp className="w-4 h-4 text-[var(--text-muted)]" />
          ) : (
            <ChevronDown className="w-4 h-4 text-[var(--text-muted)]" />
          )}
        </button>

        {showOptimize && (
          <div className="px-5 pb-5">
            <div className="h-px bg-[var(--border-gray)] mb-5" />

            {/* Optimization form */}
            <div className="grid grid-cols-4 gap-4 mb-4">
              <Input
                label={t("backtest.nTrials")}
                type="number"
                placeholder="100"
                value={optNTrials}
                onChange={(e) => setOptNTrials(e.target.value)}
              />
              <Select
                label={t("backtest.fitnessObjective")}
                value={optFitness}
                options={FITNESS_OPTIONS}
                onChange={setOptFitness}
              />
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
                  {t("backtest.trainTestSplit")} ({optTrainPct}% / {100 - optTrainPct}%)
                </label>
                <div className="flex items-center gap-3 rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] px-[14px] py-[10px]">
                  <input
                    type="range"
                    min={50}
                    max={95}
                    value={optTrainPct}
                    onChange={(e) => setOptTrainPct(parseInt(e.target.value))}
                    className="w-full accent-[var(--accent-green)]"
                  />
                </div>
              </div>
              <div className="flex items-end">
                <Button
                  variant="outline"
                  onClick={handleStartOptimization}
                  disabled={optimizing || strategyOptions.length === 0}
                  icon={optimizing ? <Loader2 className="w-3 h-3 animate-spin" /> : undefined}
                >
                  {optimizing ? t("backtest.optimizing") : t("backtest.startOptimization")}
                </Button>
              </div>
            </div>

            {/* Optimization progress */}
            {optimizing && (
              <div className="mb-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                    {t("backtest.trialProgress")} {optTrialsCompleted}/{optTotalTrials}
                  </span>
                  {optBestValue != null && (
                    <span className="text-[11px] font-medium text-[var(--accent-green)]">
                      {t("backtest.bestValue")}: {optBestValue.toFixed(4)}
                    </span>
                  )}
                </div>
                <div className="w-full h-2 rounded-full bg-[var(--bg-elevated)] overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[var(--accent-green)] transition-all duration-300"
                    style={{ width: `${optTotalTrials > 0 ? (optTrialsCompleted / optTotalTrials) * 100 : 0}%` }}
                  />
                </div>
              </div>
            )}

            {/* Optimization results */}
            {(optResult || optBestParams) && !optimizing && (
              <div className="flex flex-col gap-4">
                <div className="grid grid-cols-2 gap-4">
                  {/* Best params */}
                  <div>
                    <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase block mb-2">
                      {t("backtest.bestParams")}
                    </span>
                    <div className="rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-3">
                      {Object.entries(optResult?.best_params ?? optBestParams ?? {}).map(([k, v]) => (
                        <div key={k} className="flex justify-between py-1">
                          <span className="text-[11px] font-medium text-[var(--text-secondary)]">{k}</span>
                          <span className="text-[11px] font-bold text-[var(--text-primary)]">{String(v)}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Train/Test metrics */}
                  {optResult && (
                    <div className="flex flex-col gap-3">
                      {optResult.best_value != null && (
                        <div>
                          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase block mb-1">
                            {t("backtest.bestValue")}
                          </span>
                          <span className="font-heading text-[20px] font-bold text-[var(--accent-green)]">
                            {optResult.best_value.toFixed(4)}
                          </span>
                        </div>
                      )}
                      {optResult.train_metrics && (
                        <div>
                          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase block mb-1">
                            {t("backtest.trainMetrics")}
                          </span>
                          <div className="rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-3">
                            {Object.entries(optResult.train_metrics).map(([k, v]) => (
                              <div key={k} className="flex justify-between py-1">
                                <span className="text-[11px] font-medium text-[var(--text-secondary)]">{k}</span>
                                <span className="text-[11px] font-bold text-[var(--text-primary)]">{typeof v === "number" ? v.toFixed(4) : String(v)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                      {optResult.test_metrics && (
                        <div>
                          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase block mb-1">
                            {t("backtest.testMetrics")}
                          </span>
                          <div className="rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] p-3">
                            {Object.entries(optResult.test_metrics).map(([k, v]) => (
                              <div key={k} className="flex justify-between py-1">
                                <span className="text-[11px] font-medium text-[var(--text-secondary)]">{k}</span>
                                <span className="text-[11px] font-bold text-[var(--text-primary)]">{typeof v === "number" ? v.toFixed(4) : String(v)}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <Button
                  variant="primary"
                  onClick={handleRunWithBestParams}
                  disabled={running}
                  icon={<Play className="w-3 h-3" />}
                >
                  {t("backtest.runWithBestParams")}
                </Button>
              </div>
            )}

            {/* No result state */}
            {!optimizing && !optResult && !optBestParams && optId != null && (
              <div className="flex items-center justify-center py-6">
                <span className="text-[11px] text-[var(--text-muted)]">{t("backtest.noResult")}</span>
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
