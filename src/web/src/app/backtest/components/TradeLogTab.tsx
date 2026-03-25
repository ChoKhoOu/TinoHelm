"use client";

import { useState, useEffect, useMemo } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from "@tanstack/react-table";
import { ChevronUp, ChevronDown, ChevronsUpDown } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import type { TradeLogEntry } from "../types";

interface TradeLogTabProps {
  tradeLog: TradeLogEntry[];
}

/* ------------------------------------------------------------------ */
/*  Column helper                                                      */
/* ------------------------------------------------------------------ */

const col = createColumnHelper<TradeLogEntry>();

const columns = [
  col.accessor("opened_at", {
    header: "开仓时间",
    cell: (info) => {
      const raw = info.getValue();
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
    },
    size: 130,
  }),
  col.accessor("instrument", {
    header: "品种",
    cell: (info) => (
      <span className="text-[var(--accent-blue)] font-medium">{info.getValue()}</span>
    ),
    size: 140,
  }),
  col.accessor("side", {
    header: "方向",
    cell: (info) => {
      const side = info.getValue();
      const isBuy = side?.toUpperCase().includes("BUY") || side?.toUpperCase().includes("LONG");
      return (
        <span
          className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold ${
            isBuy
              ? "bg-[var(--accent-green-20)] text-[var(--accent-green)]"
              : "bg-[var(--accent-red-20)] text-[var(--accent-red)]"
          }`}
        >
          {isBuy ? "买入" : "卖出"}
        </span>
      );
    },
    size: 60,
  }),
  col.accessor("avg_open", {
    header: "开仓价",
    cell: (info) => {
      const v = info.getValue();
      return v != null ? v.toFixed(4) : "—";
    },
    size: 90,
  }),
  col.accessor("avg_close", {
    header: "平仓价",
    cell: (info) => {
      const v = info.getValue();
      return v != null ? v.toFixed(4) : "—";
    },
    size: 90,
  }),
  col.accessor("quantity", {
    header: "数量",
    cell: (info) => {
      const v = info.getValue();
      return v != null ? v.toFixed(4) : "—";
    },
    size: 80,
  }),
  col.accessor("realized_pnl", {
    header: "盈亏",
    cell: (info) => {
      const v = info.getValue();
      if (v == null) return "—";
      const positive = v >= 0;
      return (
        <span className={positive ? "text-[var(--accent-green)] font-medium" : "text-[var(--accent-red)] font-medium"}>
          {positive ? "+" : ""}
          {v.toFixed(2)}
        </span>
      );
    },
    size: 90,
  }),
  col.accessor("duration", {
    header: "持仓时长",
    cell: (info) => info.getValue() ?? "—",
    size: 100,
  }),
];

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

const PAGE_SIZE_OPTIONS = ["25", "50", "100"];

export function TradeLogTab({ tradeLog }: TradeLogTabProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [pageSize, setPageSize] = useState(25);

  const data = useMemo(() => tradeLog ?? [], [tradeLog]);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 25 } },
  });

  // Sync external pageSize state → table
  useEffect(() => {
    table.setPageSize(pageSize);
  }, [pageSize, table]);

  const totalRows = data.length;
  const { pageIndex } = table.getState().pagination;
  const from = pageIndex * pageSize + 1;
  const to = Math.min(from + pageSize - 1, totalRows);

  if (!tradeLog || tradeLog.length === 0) {
    return (
      <div className="flex items-center justify-center h-48">
        <span className="text-xs text-[var(--text-muted)]">暂无交易记录</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Table */}
      <div className="flex-1 min-h-0 overflow-auto">
        <table className="w-full text-xs border-collapse">
          <thead className="sticky top-0 z-10 bg-[var(--bg-card)]">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-[var(--border-gray)]">
                {hg.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      style={{ width: header.column.columnDef.size }}
                      className={`px-3 py-2 text-left font-semibold text-[var(--text-muted)] tracking-[0.4px] uppercase select-none ${
                        canSort ? "cursor-pointer hover:text-[var(--text-secondary)]" : ""
                      }`}
                      onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                    >
                      <div className="flex items-center gap-1">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort && (
                          <span className="text-[var(--text-muted)]">
                            {sorted === "asc" ? (
                              <ChevronUp className="w-3 h-3" />
                            ) : sorted === "desc" ? (
                              <ChevronDown className="w-3 h-3" />
                            ) : (
                              <ChevronsUpDown className="w-3 h-3 opacity-40" />
                            )}
                          </span>
                        )}
                      </div>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row, i) => (
              <tr
                key={row.id}
                className={`border-b border-[var(--border-gray)]/30 hover:bg-[var(--bg-subtle)]/50 transition-colors ${
                  i % 2 === 0 ? "" : "bg-[var(--bg-page)]/30"
                }`}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-1.5 text-[var(--text-secondary)]">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination bar */}
      <div className="flex items-center justify-between px-4 py-2 border-t border-[var(--border-gray)] bg-[var(--bg-card)] shrink-0">
        <span className="text-[11px] text-[var(--text-muted)]">
          {from}–{to} / {totalRows} 条记录
        </span>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-[var(--text-muted)]">每页</span>
            <Select value={String(pageSize)} onValueChange={(v: string | null) => v && setPageSize(Number(v))}>
              <SelectTrigger className="w-16 h-7 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((opt) => (
                  <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="px-2 py-1 rounded text-[11px] text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              上一页
            </button>
            <span className="text-[11px] text-[var(--text-muted)] px-1">
              {pageIndex + 1} / {table.getPageCount()}
            </span>
            <button
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="px-2 py-1 rounded text-[11px] text-[var(--text-secondary)] hover:bg-[var(--bg-subtle)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              下一页
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
