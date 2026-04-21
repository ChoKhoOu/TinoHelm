"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import { Search, ArrowLeft } from "lucide-react";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { EmptyState } from "@/components/EmptyState";
import { BacktestPagination } from "./BacktestPagination";
import type { TradeLogEntry } from "../types";
import type { BacktestRunSummary } from "./BacktestListView";
import { VIEW_BTN_CLS } from "./backtestStyles";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const PAGE_SIZE = 20;

function isLong(side: string): boolean {
  const s = side?.toUpperCase() ?? "";
  return s.includes("BUY") || s.includes("LONG");
}

function toNum(v: number | string): number {
  return typeof v === "string" ? parseFloat(v) : v;
}

function fmtDate(raw: string | undefined): string {
  if (!raw) return "—";
  try {
    return new Date(raw).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return raw;
  }
}

function fmtPrice(v: number | string): string {
  const n = toNum(v);
  return isNaN(n) ? "—" : n.toFixed(4);
}

function fmtQty(v: number | string): string {
  const n = toNum(v);
  return isNaN(n) ? "—" : n.toFixed(4);
}

interface TradeSummary {
  total: number;
  wins: number;
  losses: number;
  winRate: string;
  totalPnl: string;
  avgDuration: string;
}

function computeSummary(trades: TradeLogEntry[]): TradeSummary {
  if (trades.length === 0) {
    return { total: 0, wins: 0, losses: 0, winRate: "—", totalPnl: "—", avgDuration: "—" };
  }
  let wins = 0;
  let losses = 0;
  let totalPnl = 0;

  for (const t of trades) {
    const pnl = toNum(t.realized_pnl);
    if (!isNaN(pnl)) {
      totalPnl += pnl;
      if (pnl > 0) wins++;
      else if (pnl < 0) losses++;
    }
  }

  const winRate = trades.length > 0 ? ((wins / trades.length) * 100).toFixed(1) + "%" : "—";
  const pnlSign = totalPnl >= 0 ? "+" : "";
  const pnlStr = `${pnlSign}${totalPnl.toFixed(2)}`;

  return {
    total: trades.length,
    wins,
    losses,
    winRate,
    totalPnl: pnlStr,
    avgDuration: "—",
  };
}

/* ------------------------------------------------------------------ */
/*  Props                                                              */
/* ------------------------------------------------------------------ */

interface BacktestTradesViewProps {
  selectedRun: BacktestRunSummary;
  tradeLog: TradeLogEntry[];
  onBack: () => void;
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function PillTab<T extends string>({
  value,
  current,
  onClick,
  children,
}: {
  value: T;
  current: T;
  onClick: (v: T) => void;
  children: React.ReactNode;
}) {
  const active = value === current;
  return (
    <button
      onClick={() => onClick(value)}
      className={`font-mono text-[0.68rem] px-2.5 py-1 rounded border-0 cursor-pointer transition-colors whitespace-nowrap ${
        active
          ? "bg-secondary text-foreground shadow-sm"
          : "bg-transparent text-muted-foreground hover:text-qds-t1"
      }`}
    >
      {children}
    </button>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

type SideFilter = "all" | "long" | "short";
type ResultFilter = "all" | "win" | "loss";

export function BacktestTradesView({
  selectedRun,
  tradeLog,
  onBack,
}: BacktestTradesViewProps) {
  const [sideFilter, setSideFilter] = useState<SideFilter>("all");
  const [resultFilter, setResultFilter] = useState<ResultFilter>("all");
  const [search, setSearch] = useState("");
  const [curPage, setCurPage] = useState(1);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // ⌘K / Ctrl+K binding
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // Reset page when filters change
  // eslint-disable-next-line react-hooks/set-state-in-effect -- reason: filter change must reset pagination
  useEffect(() => { setCurPage(1); }, [sideFilter, resultFilter, search]);

  const filtered = useMemo(() => {
    let list = tradeLog;

    if (sideFilter !== "all") {
      list = list.filter((t) =>
        sideFilter === "long" ? isLong(t.side) : !isLong(t.side),
      );
    }

    if (resultFilter !== "all") {
      list = list.filter((t) => {
        const pnl = toNum(t.realized_pnl);
        if (isNaN(pnl)) return false;
        return resultFilter === "win" ? pnl > 0 : pnl < 0;
      });
    }

    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter((t) =>
        t.instrument?.toLowerCase().includes(q) ||
        t.side?.toLowerCase().includes(q) ||
        String(t.realized_pnl).includes(q) ||
        (t.opened_at ?? "").toLowerCase().includes(q),
      );
    }

    return list;
  }, [tradeLog, sideFilter, resultFilter, search]);

  const summary = useMemo(() => computeSummary(filtered), [filtered]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paginated = useMemo(() => {
    const start = (curPage - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, curPage]);

  const isEmpty = tradeLog.length === 0;
  const noMatch = !isEmpty && filtered.length === 0;

  return (
    <div data-view="trades" className="flex flex-col gap-4 mt-5">

      {/* ── Header ── */}
      <div className="flex items-center gap-3 pb-4 border-b border-border animate-qds-fade-up [animation-delay:0ms]">
        <button className={`${VIEW_BTN_CLS} text-[0.72rem]`} onClick={onBack}>
          <ArrowLeft className="w-3.5 h-3.5" />
          返回
        </button>
        <div>
          <div className="font-mono text-base font-semibold">
            所有交易
          </div>
          <div className="font-mono text-[0.68rem] text-muted-foreground">
            {selectedRun.strategy_name} · {selectedRun.run_id.slice(0, 8)}
          </div>
        </div>
      </div>

      {/* ── Summary strip (always rendered if tradeLog non-empty) ── */}
      {!isEmpty && (
        <div className="grid grid-cols-6 gap-0 border border-border rounded-lg bg-card overflow-hidden animate-qds-fade-up [animation-delay:80ms]">
          {[
            { label: "总交易数", value: String(summary.total) },
            { label: "盈利交易", value: String(summary.wins) },
            { label: "亏损交易", value: String(summary.losses) },
            { label: "胜率", value: summary.winRate },
            {
              label: "总盈亏",
              value: summary.totalPnl,
              valueClass:
                summary.totalPnl.startsWith("+")
                  ? "text-qds-success"
                  : summary.totalPnl.startsWith("-")
                  ? "text-destructive"
                  : "text-foreground",
            },
            { label: "平均持仓", value: summary.avgDuration },
          ].map((cell, i) => (
            <div
              key={cell.label}
              data-summary-cell
              className={`px-4 py-3 flex flex-col gap-0.5${i > 0 ? " border-l border-border" : ""}`}
            >
              <div className="font-mono text-[0.56rem] tracking-widest uppercase text-muted-foreground">
                {cell.label}
              </div>
              <div
                className={`font-mono text-sm font-semibold tabular-nums ${cell.valueClass ?? "text-foreground"}`}
              >
                {cell.value}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Filter bar ── */}
      {!isEmpty && (
        <div className="flex items-center gap-3 flex-wrap animate-qds-fade-up [animation-delay:160ms]">
          {/* Side filter */}
          <div className="inline-flex gap-[2px] bg-input rounded-md p-[3px]">
            <PillTab value="all" current={sideFilter} onClick={setSideFilter}>全部</PillTab>
            <PillTab value="long" current={sideFilter} onClick={setSideFilter}>多头</PillTab>
            <PillTab value="short" current={sideFilter} onClick={setSideFilter}>空头</PillTab>
          </div>

          {/* Result filter */}
          <div className="inline-flex gap-[2px] bg-input rounded-md p-[3px]">
            <PillTab value="all" current={resultFilter} onClick={setResultFilter}>全部</PillTab>
            <PillTab value="win" current={resultFilter} onClick={setResultFilter}>盈利</PillTab>
            <PillTab value="loss" current={resultFilter} onClick={setResultFilter}>亏损</PillTab>
          </div>

          {/* Search */}
          <div className="relative flex items-center ml-auto">
            <Search className="absolute left-2.5 w-4 h-4 text-muted-foreground pointer-events-none" />
            <input
              ref={searchInputRef}
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索..."
              className="qds-input pl-8 pr-12 h-8 text-xs w-52"
            />
            <kbd className="absolute right-2 font-mono text-[0.6rem] text-muted-foreground bg-secondary border border-border rounded px-1 py-0.5 pointer-events-none">
              ⌘K
            </kbd>
          </div>
        </div>
      )}

      {/* ── Table area ── */}
      {isEmpty ? (
        <div className="border border-border rounded-lg bg-card animate-qds-fade-up [animation-delay:160ms]">
          <EmptyState
            variant="first-use"
            size="section"
            title="此回测暂无交易记录"
            description="回测未产生任何已平仓交易"
          />
        </div>
      ) : noMatch ? (
        <>
          <div className="border border-border rounded-lg bg-card animate-qds-fade-up [animation-delay:160ms]">
            <EmptyState
              variant="no-results"
              size="section"
              title="无匹配交易"
              description="尝试调整筛选条件或搜索关键词"
            />
          </div>
        </>
      ) : (
        <div className="border border-border rounded-lg bg-card overflow-hidden animate-qds-fade-up [animation-delay:160ms]">
          <Table className="w-full text-xs border-collapse">
            <TableHeader className="bg-card">
              <TableRow className="border-b border-border">
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">ID</TableHead>
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">日期</TableHead>
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">方向</TableHead>
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">入场</TableHead>
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">出场</TableHead>
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">仓位</TableHead>
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">盈亏</TableHead>
                <TableHead className="px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase text-[0.65rem] whitespace-nowrap">持仓</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {paginated.map((trade, i) => {
                const pnl = toNum(trade.realized_pnl);
                const pnlSign = isNaN(pnl) ? "zero" : pnl > 0 ? "positive" : pnl < 0 ? "negative" : "zero";
                const side = isLong(trade.side) ? "long" : "short";
                const globalIdx = (curPage - 1) * PAGE_SIZE + i + 1;

                return (
                  <TableRow
                    key={`${trade.opened_at}-${i}`}
                    data-side={side}
                    data-pnl-sign={pnlSign}
                    className={`border-b border-border hover:bg-secondary transition-colors ${
                      i % 2 === 0 ? "" : "bg-background"
                    }`}
                  >
                    {/* ID */}
                    <TableCell className="px-3 py-1.5 font-mono text-[0.65rem] text-muted-foreground tabular-nums">
                      #{globalIdx}
                    </TableCell>
                    {/* 日期 */}
                    <TableCell className="px-3 py-1.5 font-mono text-[0.7rem] text-muted-foreground whitespace-nowrap">
                      {fmtDate(trade.opened_at)}
                    </TableCell>
                    {/* 方向 */}
                    <TableCell className="px-3 py-1.5">
                      <span
                        className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          side === "long"
                            ? "bg-qds-success-dim text-qds-success"
                            : "bg-qds-danger-dim text-destructive"
                        }`}
                      >
                        {side === "long" ? "多" : "空"}
                      </span>
                    </TableCell>
                    {/* 入场 */}
                    <TableCell className="px-3 py-1.5 font-mono text-[0.7rem] text-muted-foreground tabular-nums">
                      {fmtPrice(trade.avg_open)}
                    </TableCell>
                    {/* 出场 */}
                    <TableCell className="px-3 py-1.5 font-mono text-[0.7rem] text-muted-foreground tabular-nums">
                      {fmtPrice(trade.avg_close)}
                    </TableCell>
                    {/* 仓位 */}
                    <TableCell className="px-3 py-1.5 font-mono text-[0.7rem] text-muted-foreground tabular-nums">
                      {fmtQty(trade.quantity)}
                    </TableCell>
                    {/* 盈亏 */}
                    <TableCell className="px-3 py-1.5 font-mono text-[0.7rem] font-medium tabular-nums">
                      {isNaN(pnl) ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <span
                          className={
                            pnl > 0 ? "text-qds-success" : pnl < 0 ? "text-destructive" : "text-muted-foreground"
                          }
                        >
                          {pnl >= 0 ? "+" : ""}
                          {pnl.toFixed(2)}
                        </span>
                      )}
                    </TableCell>
                    {/* 持仓 */}
                    <TableCell className="px-3 py-1.5 font-mono text-[0.7rem] text-muted-foreground">
                      {trade.duration ?? "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>

          {/* Pagination */}
          <div className="animate-qds-fade-up [animation-delay:240ms]">
            <BacktestPagination
              curPage={curPage}
              totalPages={totalPages}
              total={filtered.length}
              pageSize={PAGE_SIZE}
              onPageChange={setCurPage}
            />
          </div>
        </div>
      )}
    </div>
  );
}
