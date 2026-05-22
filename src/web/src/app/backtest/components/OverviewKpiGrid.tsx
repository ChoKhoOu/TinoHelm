"use client";

import { HelpTip } from "@/components/qds";
import type { BacktestResult } from "../types";
import {
  STAT_CARD_CLS,
  STAT_LABEL_CLS,
  STAT_VALUE_CLS,
  STAT_SUB_CLS,
  SEC_CLS,
  SectionLabel,
  fmt,
  fmtSigned,
  fmtCurrency,
} from "./OverviewHelpers";

interface OverviewKpiGridProps {
  s: BacktestResult["statistics"];
  totalPnl: number;
  totalRetPct: number;
  isPnlPositive: boolean;
  maxDdPct: number | null;
}

/**
 * Core + secondary KPI metric grid. 11 stat cards in a single auto-fill grid.
 */
export function OverviewKpiGrid({
  s,
  totalPnl,
  totalRetPct,
  isPnlPositive,
  maxDdPct,
}: OverviewKpiGridProps) {
  return (
    <div className={SEC_CLS}>
      <SectionLabel>Core Metrics</SectionLabel>
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))" }}>
        {/* Total PnL */}
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>
            Total PnL
            <HelpTip text="总盈亏" />
          </div>
          <div className={`${STAT_VALUE_CLS} ${isPnlPositive ? "text-qds-success" : "text-destructive"}`}>
            {isPnlPositive ? "+" : ""}{fmtCurrency(totalPnl)}
          </div>
          <div className={`${STAT_SUB_CLS} ${isPnlPositive ? "text-qds-success" : "text-destructive"}`}>
            {fmtSigned(totalRetPct, 2)}%
          </div>
        </div>

        {/* Sharpe */}
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>
            Sharpe
            <HelpTip text="夏普比率。>1 可接受，>2 优秀" />
          </div>
          <div className={STAT_VALUE_CLS}>
            {s.sharpe_ratio !== null ? fmt(s.sharpe_ratio, 2) : "—"}
          </div>
        </div>

        {/* Max DD */}
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>
            Max DD
            <HelpTip text="最大回撤" />
          </div>
          <div className={`${STAT_VALUE_CLS} text-destructive`}>
            {maxDdPct !== null ? `-${fmt(maxDdPct, 1)}%` : "—"}
          </div>
        </div>

        {/* Win Rate */}
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>
            Win Rate
            <HelpTip text="胜率" />
          </div>
          <div className={STAT_VALUE_CLS}>
            {fmt(s.win_rate * 100, 1)}%
          </div>
        </div>

        {/* Profit Factor */}
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>
            Profit Factor
            <HelpTip text="盈亏比。>1.5 良好" />
          </div>
          <div className={STAT_VALUE_CLS}>
            {s.profit_factor !== null ? fmt(s.profit_factor, 2) : "—"}
          </div>
        </div>
        {/* Secondary metrics — same grid, no section break */}
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>Sortino<HelpTip text="索提诺比率。只惩罚下行波动，>2 优秀" /></div>
          <div className={STAT_VALUE_CLS}>{s.sortino_ratio !== null ? fmt(s.sortino_ratio, 2) : "—"}</div>
        </div>
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>Calmar<HelpTip text="卡尔马比率。年化收益÷最大回撤，>3 优秀" /></div>
          <div className={STAT_VALUE_CLS}>{s.calmar_ratio !== null ? fmt(s.calmar_ratio, 2) : "—"}</div>
        </div>
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>Annual Return<HelpTip text="年化收益率" /></div>
          <div className={`${STAT_VALUE_CLS} ${s.annual_return !== null && s.annual_return >= 0 ? "text-qds-success" : "text-destructive"}`}>
            {s.annual_return !== null ? `${fmtSigned(s.annual_return * 100, 2)}%` : "—"}
          </div>
        </div>
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>Volatility<HelpTip text="年化波动率。衡量收益的离散程度" /></div>
          <div className={STAT_VALUE_CLS}>{s.returns_volatility !== null ? `${fmt(s.returns_volatility * 100, 2)}%` : "—"}</div>
        </div>
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>Expectancy<HelpTip text="期望值。每笔交易的平均预期收益" /></div>
          <div className={`${STAT_VALUE_CLS} ${s.expectancy !== null && s.expectancy >= 0 ? "text-qds-success" : "text-destructive"}`}>
            {s.expectancy !== null ? fmtCurrency(s.expectancy) : "—"}
          </div>
        </div>
        <div className={STAT_CARD_CLS}>
          <div className={STAT_LABEL_CLS}>Total Fees<HelpTip text="总手续费" /></div>
          <div className={STAT_VALUE_CLS}>{s.total_fees !== null ? fmtCurrency(s.total_fees) : "—"}</div>
        </div>
      </div>
    </div>
  );
}
