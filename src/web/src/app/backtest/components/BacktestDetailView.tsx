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
}

export function BacktestDetailView({
  selectedRun,
  selectedRunId,
  activeTab,
  setActiveTab,
  progressPct,
  progressMessage,
  tradeLog,
  onBack,
}: BacktestDetailViewProps) {
  const isCompleted = selectedRun.status === "completed";

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
      <div className="flex items-center gap-4 mt-5 mb-4 pb-4 border-b border-border">
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

      {/* Pill tab bar */}
      <div className="sticky top-0 z-20 bg-background py-2.5 border-b border-transparent -mx-8 px-8">
        <div className="inline-flex gap-[2px] bg-input rounded-md p-[3px] w-fit">
          {DETAIL_TABS.map((tab) => (
            <button
              key={tab.key}
              className={`font-mono text-[0.7rem] px-3 py-1.5 rounded border-0 cursor-pointer transition-colors whitespace-nowrap ${
                activeTab === tab.key
                  ? "bg-secondary text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.15)]"
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
      <div className="mt-4">
        {activeTab === "overview" && (
          isCompleted ? <OverviewTab runId={selectedRunId} /> : renderPlaceholder()
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
