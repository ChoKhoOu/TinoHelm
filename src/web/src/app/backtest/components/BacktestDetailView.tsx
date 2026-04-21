"use client";

import type { TradeLogEntry } from "../types";
import type { BacktestRunSummary } from "./BacktestListView";
import { BacktestCopyableId, BacktestRunningPlaceholder } from "./BacktestRunningPlaceholder";
import { OverviewTab } from "./OverviewTab";
import { TearsheetTab } from "./TearsheetTab";
import { TradeLogTab } from "./TradeLogTab";
import { ReportsTab } from "./ReportsTab";
import { PerformanceTab } from "./PerformanceTab";
import { TradesTab } from "./TradesTab";
import { RobustnessTab } from "./RobustnessTab";
import { ACT_BTN_CLS, VIEW_BTN_CLS } from "./backtestStyles";

const DETAIL_TABS = [
  { key: "overview", label: "Overview" },
  { key: "performance", label: "Performance" },
  { key: "trades", label: "Trades" },
  { key: "robustness", label: "Robustness" },
  { key: "tearsheet", label: "Report" },
  { key: "tradelog", label: "Trade Log" },
  { key: "reports", label: "Data Tables" },
];

interface BacktestDetailViewProps {
  selectedRun: BacktestRunSummary;
  selectedRunId: string;
  activeTab: string;
  setActiveTab: (key: string) => void;
  progressPct: number;
  progressMessage: string | undefined;
  tradeLog: TradeLogEntry[];
  onBack: () => void;
  onViewAllTrades?: (runId: string) => void;
}

/* ------------------------------------------------------------------ */
/*  Detail KPI Grid helpers                                            */
/* ------------------------------------------------------------------ */

function fmtAbsThousands(n: number): string {
  return Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

interface KpiCellData {
  label: string;
  value: string;
  valueClass: string;
  sub?: string;
}

function buildKpiItems(s: BacktestRunSummary["result_summary"]): KpiCellData[] {
  const dash: KpiCellData = { label: "", value: "—", valueClass: "text-muted-foreground" };

  if (!s) {
    return [
      { ...dash, label: "总盈亏" },
      { ...dash, label: "总收益率" },
      { ...dash, label: "Sharpe" },
      { ...dash, label: "Calmar" },
      { ...dash, label: "胜率" },
      { ...dash, label: "交易笔数" },
    ];
  }

  // 1. 总盈亏
  const pnlCell: KpiCellData = (() => {
    if (s.total_pnl == null) return { label: "总盈亏", value: "—", valueClass: "text-muted-foreground" };
    const positive = s.total_pnl >= 0;
    return {
      label: "总盈亏",
      value: positive ? `+$${fmtAbsThousands(s.total_pnl)}` : `-$${fmtAbsThousands(s.total_pnl)}`,
      valueClass: positive ? "text-qds-success" : "text-destructive",
      sub: s.total_return_pct != null
        ? `${s.total_return_pct >= 0 ? "+" : ""}${s.total_return_pct.toFixed(1)}%`
        : undefined,
    };
  })();

  // 2. 总收益率
  const retCell: KpiCellData = (() => {
    if (s.total_return_pct == null) return { label: "总收益率", value: "—", valueClass: "text-muted-foreground" };
    const positive = s.total_return_pct >= 0;
    return {
      label: "总收益率",
      value: `${positive ? "+" : ""}${s.total_return_pct.toFixed(1)}%`,
      valueClass: positive ? "text-qds-success" : "text-destructive",
    };
  })();

  // 3. Sharpe
  const sharpeCell: KpiCellData = (() => {
    if (s.sharpe_ratio == null) return { label: "Sharpe", value: "—", valueClass: "text-muted-foreground" };
    return {
      label: "Sharpe",
      value: s.sharpe_ratio.toFixed(2),
      valueClass: "text-foreground",
      sub: s.sharpe_ratio >= 1 ? "良好" : "一般",
    };
  })();

  // 4. Calmar
  const calmarCell: KpiCellData = (() => {
    if (s.calmar_ratio == null) return { label: "Calmar", value: "—", valueClass: "text-muted-foreground" };
    return {
      label: "Calmar",
      value: s.calmar_ratio.toFixed(2),
      valueClass: "text-foreground",
    };
  })();

  // 5. 胜率
  const winRateCell: KpiCellData = (() => {
    if (s.win_rate == null) return { label: "胜率", value: "—", valueClass: "text-muted-foreground" };
    return {
      label: "胜率",
      value: `${(s.win_rate * 100).toFixed(1)}%`,
      valueClass: "text-foreground",
    };
  })();

  // 6. 交易笔数
  const tradesCell: KpiCellData = (() => {
    if (s.total_trades == null) return { label: "交易笔数", value: "—", valueClass: "text-muted-foreground" };
    return {
      label: "交易笔数",
      value: String(s.total_trades),
      valueClass: "text-foreground",
    };
  })();

  return [pnlCell, retCell, sharpeCell, calmarCell, winRateCell, tradesCell];
}

/* ------------------------------------------------------------------ */

export function BacktestDetailView({
  selectedRun,
  selectedRunId,
  activeTab,
  setActiveTab,
  progressPct,
  progressMessage,
  tradeLog,
  onBack,
  onViewAllTrades,
}: BacktestDetailViewProps) {
  const isCompleted = selectedRun.status === "completed";
  const kpiItems = buildKpiItems(
    isCompleted && selectedRun.result_summary ? selectedRun.result_summary : null,
  );

  const renderPlaceholder = () => (
    <BacktestRunningPlaceholder
      status={selectedRun.status}
      pct={progressPct}
      message={progressMessage}
    />
  );

  return (
    <div>
      {/* Detail top bar */}
      <div className="flex items-center gap-4 mt-5 mb-4 pb-4 border-b border-border animate-qds-fade-up [animation-delay:0ms]">
        <button
          className={`${VIEW_BTN_CLS} text-[0.72rem]`}
          onClick={onBack}
        >
          <span className="transition-transform">&larr;</span> 返回
        </button>
        <div>
          <div className="flex items-center gap-2 font-mono text-base font-semibold">
            {selectedRun.strategy_name}
            <BacktestCopyableId runId={selectedRun.run_id} />
          </div>
          <div className="text-[0.72rem] text-muted-foreground">
            {(() => {
              const syms = selectedRun.symbol.split(",").map((s) => s.trim()).filter(Boolean);
              return syms.length <= 3 ? syms.join(", ") : `${syms.slice(0, 3).join(", ")} +${syms.length - 3}`;
            })()} · {selectedRun.interval} · {selectedRun.start_date?.slice(0, 10)} → {selectedRun.end_date?.slice(0, 10)}
          </div>
        </div>
        <div className="ml-auto flex gap-1.5">
          <button className={ACT_BTN_CLS}>导出</button>
          <button className={ACT_BTN_CLS}>克隆</button>
          <button className={`${ACT_BTN_CLS} !text-destructive`}>删除</button>
        </div>
      </div>

      {/* 6-column KPI grid */}
      <div
        className="grid grid-cols-6 gap-0 border border-border rounded-lg bg-card overflow-hidden mb-4 animate-qds-fade-up [animation-delay:80ms]"
        data-kpi-grid
      >
        {kpiItems.map((item, i) => (
          <div
            key={item.label}
            data-kpi-cell
            className={`px-4 py-3 flex flex-col gap-0.5${i > 0 ? " border-l border-border" : ""}`}
          >
            <div className="font-mono text-[0.56rem] tracking-widest uppercase text-muted-foreground">
              {item.label}
            </div>
            <div className={`font-mono text-sm font-semibold tabular-nums ${item.valueClass}`}>
              {item.value}
            </div>
            {item.sub && (
              <div className="font-mono text-[0.65rem] text-muted-foreground">
                {item.sub}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Pill tab bar */}
      <div className="sticky top-0 z-20 bg-background py-2.5 border-b border-transparent -mx-8 px-8 animate-qds-fade-up [animation-delay:160ms]">
        <div className="inline-flex gap-[2px] bg-input rounded-md p-[3px] w-fit">
          {DETAIL_TABS.map((tab) => (
            <button
              key={tab.key}
              className={`font-mono text-[0.7rem] px-3 py-1.5 rounded border-0 cursor-pointer transition-colors whitespace-nowrap ${
                activeTab === tab.key
                  ? "bg-secondary text-foreground shadow-sm"
                  : "bg-transparent text-muted-foreground hover:text-qds-t1"
              }`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="mt-4 animate-qds-fade-up [animation-delay:240ms]">
        {activeTab === "overview" && (
          isCompleted
            ? <OverviewTab runId={selectedRunId} onViewAllTrades={() => onViewAllTrades?.(selectedRun.run_id)} />
            : renderPlaceholder()
        )}
        {activeTab === "performance" && (
          isCompleted ? <PerformanceTab runId={selectedRunId} /> : renderPlaceholder()
        )}
        {activeTab === "trades" && (
          isCompleted ? <TradesTab runId={selectedRunId} /> : renderPlaceholder()
        )}
        {activeTab === "robustness" && (
          isCompleted ? <RobustnessTab runId={selectedRunId} /> : renderPlaceholder()
        )}
        {activeTab === "tearsheet" && (
          isCompleted ? <TearsheetTab runId={selectedRunId} /> : renderPlaceholder()
        )}
        {activeTab === "tradelog" && (
          isCompleted ? <TradeLogTab tradeLog={tradeLog} /> : renderPlaceholder()
        )}
        {activeTab === "reports" && (
          isCompleted ? <ReportsTab runId={selectedRunId} /> : renderPlaceholder()
        )}
      </div>
    </div>
  );
}
