"use client";

import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import type { BacktestResult, DrawdownPeriod, PerInstrumentEntry } from "../types";
import {
  CARD_CLS,
  CARD_BODY_CLS,
  SEC_CLS,
  SectionLabel,
  fmt,
  fmtSigned,
  stripVenue,
  pnlColor,
} from "./OverviewHelpers";

/* ------------------------------------------------------------------ */
/*  Top Trades                                                         */
/* ------------------------------------------------------------------ */

export function TopTrades({ tradeLog }: { tradeLog: BacktestResult["trade_log"] }) {
  if (!tradeLog || tradeLog.length < 2) return null;

  const sorted = [...tradeLog].sort((a, b) => Number(b.realized_pnl) - Number(a.realized_pnl));
  const best = sorted.slice(0, 5).filter((t) => Number(t.realized_pnl) > 0);
  const worst = sorted.slice(-5).reverse().filter((t) => Number(t.realized_pnl) < 0);

  if (best.length === 0 && worst.length === 0) return null;

  const renderTrade = (t: (typeof tradeLog)[0], idx: number) => {
    const pnl = Number(t.realized_pnl);
    return (
      <div key={idx} className="flex items-center justify-between py-1">
        <div className="flex items-center gap-2">
          <span className="text-xs text-primary font-medium w-28 truncate">{stripVenue(t.instrument)}</span>
          <span className={`text-[10px] font-medium ${t.side === "BUY" ? "text-qds-success" : "text-destructive"}`}>
            {t.side}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className={`text-xs font-semibold ${pnlColor(pnl)}`}>{fmtSigned(pnl, 2)}</span>
          {t.duration && <span className="text-[10px] text-muted-foreground">{t.duration}</span>}
        </div>
      </div>
    );
  };

  return (
    <div className={SEC_CLS}>
      <SectionLabel>标志性交易</SectionLabel>
      <div className={CARD_CLS}>
        <div className={CARD_BODY_CLS}>
          <div className="flex gap-4">
            {best.length > 0 && (
              <div className="flex-1 flex flex-col gap-1">
                <span className="text-[10px] font-medium text-qds-success mb-1">最佳交易</span>
                {best.map(renderTrade)}
              </div>
            )}
            {best.length > 0 && worst.length > 0 && (
              <div className="w-px bg-border shrink-0" />
            )}
            {worst.length > 0 && (
              <div className="flex-1 flex flex-col gap-1">
                <span className="text-[10px] font-medium text-destructive mb-1">最差交易</span>
                {worst.map(renderTrade)}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Notable Drawdowns                                                  */
/* ------------------------------------------------------------------ */

export function DrawdownTable({ periods, topN }: { periods: DrawdownPeriod[]; topN?: number }) {
  if (!periods || periods.length === 0) return null;
  const top5 = periods.slice(0, topN ?? 5);

  return (
    <div className={SEC_CLS}>
      <SectionLabel>显著回撤</SectionLabel>
      <div className={CARD_CLS}>
        <div className={CARD_BODY_CLS}>
          <Table className="w-full text-xs">
          <TableHeader>
            <TableRow className="text-muted-foreground">
              <TableHead className="text-left py-1.5 pr-3 font-medium">开始日期</TableHead>
              <TableHead className="text-right py-1.5 pr-3 font-medium">最大回撤</TableHead>
              <TableHead className="text-right py-1.5 pr-3 font-medium">持续天数</TableHead>
              <TableHead className="text-right py-1.5 font-medium">恢复</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {top5.map((dd, i) => (
              <TableRow key={i}>
                <TableCell className="py-1.5 pr-3 text-muted-foreground">{dd.start.slice(0, 10)}</TableCell>
                <TableCell className="py-1.5 pr-3 text-right text-destructive font-medium">
                  {fmt(dd.max_drawdown_pct, 2)}%
                </TableCell>
                <TableCell className="py-1.5 pr-3 text-right text-muted-foreground">{dd.duration_days}天</TableCell>
                <TableCell className="py-1.5 text-right">
                  {dd.recovery_days !== null ? (
                    <span className="text-qds-success">{dd.recovery_days}天</span>
                  ) : (
                    <span className="text-muted-foreground">未恢复</span>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Per-Instrument Breakdown                                           */
/* ------------------------------------------------------------------ */

export function InstrumentBreakdown({ data }: { data: Record<string, PerInstrumentEntry> }) {
  const entries = Object.entries(data)
    .map(([key, val]) => ({ ...val, instrument: stripVenue(key) }))
    .sort((a, b) => b.total_pnl - a.total_pnl);

  if (entries.length === 0) return null;

  const maxAbsPnl = Math.max(...entries.map((e) => Math.abs(e.total_pnl)), 1);

  return (
    <div className={SEC_CLS}>
      <SectionLabel>品种分解</SectionLabel>
      <div className={CARD_CLS}>
        <div className={CARD_BODY_CLS}>
          <Table className="w-full text-xs">
          <TableHeader>
            <TableRow className="text-muted-foreground hover:bg-transparent">
              <TableHead className="text-left py-2 pr-3 font-medium">品种</TableHead>
              <TableHead className="text-right py-2 pr-3 font-medium">交易数</TableHead>
              <TableHead className="text-right py-2 pr-3 font-medium">胜率</TableHead>
              <TableHead className="text-right py-2 pr-3 font-medium">盈亏</TableHead>
              <TableHead className="text-left py-2 pr-3 pl-2 font-medium w-24"></TableHead>
              <TableHead className="text-right py-2 pr-3 font-medium">夏普</TableHead>
              <TableHead className="text-right py-2 font-medium">最大回撤</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((row) => {
              const barWidth = Math.round((Math.abs(row.total_pnl) / maxAbsPnl) * 100);
              return (
                <TableRow key={row.instrument} className="hover:bg-secondary">
                  <TableCell className="py-1.5 pr-3 text-primary font-medium">{row.instrument}</TableCell>
                  <TableCell className="py-1.5 pr-3 text-right text-muted-foreground">{row.total_trades}</TableCell>
                  <TableCell className="py-1.5 pr-3 text-right">
                    <span className={(row.win_rate ?? 0) >= 0.5 ? "text-qds-success" : "text-destructive"}>
                      {fmt((row.win_rate ?? 0) * 100, 1)}%
                    </span>
                  </TableCell>
                  <TableCell className="py-1.5 pr-3 text-right">
                    <span className={pnlColor(row.total_pnl)}>
                      {fmtSigned(row.total_pnl, 2)}
                    </span>
                  </TableCell>
                  <TableCell className="py-1.5 pr-3 pl-2">
                    <div className="h-2 rounded-full overflow-hidden bg-secondary">
                      <div
                        className={`h-full rounded-full ${row.total_pnl >= 0 ? "bg-qds-success" : "bg-destructive"}`}
                        style={{ width: `${barWidth}%` }}
                      />
                    </div>
                  </TableCell>
                  <TableCell className="py-1.5 pr-3 text-right text-muted-foreground">{fmt(row.sharpe_ratio, 2)}</TableCell>
                  <TableCell className="py-1.5 text-right">
                    {row.max_drawdown !== null && Math.abs(row.max_drawdown) > 0.0001 ? (
                      <span className="text-destructive">{fmt(Math.abs(row.max_drawdown) * 100, 1)}%</span>
                    ) : "—"}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Correlation Matrix                                                 */
/* ------------------------------------------------------------------ */

export function CorrelationMatrix({ data }: { data: Record<string, Record<string, number>> }) {
  const rawKeys = Object.keys(data);
  if (rawKeys.length < 2) return null;

  const pairs: { a: string; b: string; corr: number }[] = [];
  for (let i = 0; i < rawKeys.length; i++) {
    for (let j = i + 1; j < rawKeys.length; j++) {
      const corr = data[rawKeys[i]]?.[rawKeys[j]];
      if (corr !== undefined) {
        pairs.push({ a: stripVenue(rawKeys[i]), b: stripVenue(rawKeys[j]), corr });
      }
    }
  }

  const sorted = [...pairs].sort((a, b) => b.corr - a.corr);
  const top = sorted.slice(0, 3);
  const bottom = [...pairs].sort((a, b) => a.corr - b.corr).slice(0, 3);

  const corrColor = (v: number) => {
    if (v > 0.7) return "text-destructive";
    if (v > 0.3) return "text-muted-foreground";
    if (v < 0) return "text-qds-success";
    return "text-primary";
  };

  return (
    <div className={SEC_CLS}>
      <SectionLabel>品种相关性</SectionLabel>
      <div className={CARD_CLS}>
        <div className={CARD_BODY_CLS}>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1">
              <span className="text-[10px] font-medium text-destructive mb-1">高相关 (风险集中)</span>
              {top.map((p, i) => (
                <div key={i} className="flex items-center justify-between py-0.5">
                  <span className="text-xs text-muted-foreground">{p.a} / {p.b}</span>
                  <span className={`text-xs font-medium ${corrColor(p.corr)}`}>{fmtSigned(p.corr, 3)}</span>
                </div>
              ))}
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-[10px] font-medium text-qds-success mb-1">低相关 (分散化)</span>
              {bottom.map((p, i) => (
                <div key={i} className="flex items-center justify-between py-0.5">
                  <span className="text-xs text-muted-foreground">{p.a} / {p.b}</span>
                  <span className={`text-xs font-medium ${corrColor(p.corr)}`}>{fmtSigned(p.corr, 3)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
