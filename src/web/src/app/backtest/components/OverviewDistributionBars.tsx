"use client";

import type { BacktestResult } from "../types";
import { CARD_CLS, CARD_HEADER_CLS, CARD_BODY_CLS, fmt } from "./OverviewHelpers";

/* ------------------------------------------------------------------ */
/*  Win/Loss Distribution Bar                                          */
/* ------------------------------------------------------------------ */

export function WinLossBar({ s }: { s: BacktestResult["statistics"] }) {
  const total = s.winning_trades + s.losing_trades;
  if (total === 0) return null;
  const winPct = (s.winning_trades / total) * 100;

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>胜负分布</div>
      <div className={`${CARD_BODY_CLS} flex flex-col gap-3`}>
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-qds-success">{s.winning_trades}W</span>
          <div className="flex-1 h-3 rounded-full overflow-hidden bg-qds-danger-dim flex">
            <div
              className="h-full rounded-l-full bg-qds-success"
              style={{ width: `${winPct}%` }}
            />
            <div className="h-full flex-1 bg-destructive" />
          </div>
          <span className="text-xs font-medium text-destructive">{s.losing_trades}L</span>
        </div>
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>总计 {total} 笔 · 胜率 {fmt(winPct, 1)}%</span>
          <span>连胜 {s.winning_streak} / 连负 {s.losing_streak}</span>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Long/Short Distribution Bar                                        */
/* ------------------------------------------------------------------ */

export function LongShortBar({ s }: { s: BacktestResult["statistics"] }) {
  const longPct = s.long_pct !== null ? s.long_pct * 100 : null;
  const shortPct = s.short_pct !== null ? s.short_pct * 100 : null;
  if (longPct === null || shortPct === null) return null;

  return (
    <div className={CARD_CLS}>
      <div className={CARD_HEADER_CLS}>多空分布</div>
      <div className={`${CARD_BODY_CLS} flex flex-col gap-3`}>
        <div className="flex items-center gap-3">
          <span className="text-xs font-medium text-primary">做多</span>
          <div className="flex-1 h-3 rounded-full overflow-hidden flex">
            <div
              className="h-full rounded-l-full bg-primary"
              style={{ width: `${longPct}%` }}
            />
            <div className="h-full flex-1 bg-qds-warning" />
          </div>
          <span className="text-xs font-medium text-qds-warning">做空</span>
        </div>
        <div className="flex items-center justify-between text-[10px] text-muted-foreground">
          <span>做多 {fmt(longPct, 1)}%</span>
          <span>做空 {fmt(shortPct, 1)}%</span>
        </div>
      </div>
    </div>
  );
}
