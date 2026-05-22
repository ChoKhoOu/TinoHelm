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
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

import type { TradeLogEntry } from "../types";

interface TradeLogTabProps {
  tradeLog: TradeLogEntry[];
}

/* ------------------------------------------------------------------ */
/*  Column helper                                                      */
/* ------------------------------------------------------------------ */

/** Parse duration string like "1d 2h 3m 50s" into total seconds */
function parseDurationToSeconds(dur: string | null | undefined): number {
  if (!dur) return 0;
  let total = 0;
  const d = dur.match(/(\d+)d/);
  const h = dur.match(/(\d+)h/);
  const m = dur.match(/(\d+)m/);
  const s = dur.match(/(\d+)s/);
  if (d) total += parseInt(d[1]) * 86400;
  if (h) total += parseInt(h[1]) * 3600;
  if (m) total += parseInt(m[1]) * 60;
  if (s) total += parseInt(s[1]);
  return total;
}

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
      <span className="text-primary font-medium">{info.getValue()}</span>
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
              ? "bg-qds-success-dim text-qds-success"
              : "bg-qds-danger-dim text-destructive"
          }`}
        >
          {isBuy ? "买入" : "卖出"}
        </span>
      );
    },
    size: 60,
  }),
  col.accessor((row) => Number(row.avg_open), {
    id: "avg_open",
    header: "开仓价",
    cell: (info) => {
      const v = info.getValue();
      return !isNaN(v) ? v.toFixed(4) : "—";
    },
    sortingFn: "basic",
    size: 90,
  }),
  col.accessor((row) => Number(row.avg_close), {
    id: "avg_close",
    header: "平仓价",
    cell: (info) => {
      const v = info.getValue();
      return !isNaN(v) ? v.toFixed(4) : "—";
    },
    sortingFn: "basic",
    size: 90,
  }),
  col.accessor((row) => Number(row.quantity), {
    id: "quantity",
    header: "数量",
    cell: (info) => {
      const v = info.getValue();
      return !isNaN(v) ? v.toFixed(4) : "—";
    },
    sortingFn: "basic",
    size: 80,
  }),
  col.accessor((row) => Number(row.realized_pnl), {
    id: "realized_pnl",
    header: "盈亏",
    cell: (info) => {
      const v = info.getValue();
      if (isNaN(v)) return "—";
      const positive = v >= 0;
      return (
        <span className={positive ? "text-qds-success font-medium" : "text-destructive font-medium"}>
          {positive ? "+" : ""}
          {v.toFixed(2)}
        </span>
      );
    },
    sortingFn: "basic",
    size: 90,
  }),
  col.accessor((row) => parseDurationToSeconds(row.duration), {
    id: "duration",
    header: "持仓时长",
    cell: (info) => {
      const row = info.row.original;
      return row.duration ?? "—";
    },
    sortingFn: "basic",
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
        <span className="text-xs text-muted-foreground">暂无交易记录</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Table */}
      <div className="flex-1 min-h-0 overflow-auto">
        <Table className="w-full text-xs border-collapse">
          <TableHeader className="sticky top-0 z-10 bg-card">
            {table.getHeaderGroups().map((hg) => (
              <TableRow key={hg.id} className="border-b">
                {hg.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <TableHead
                      key={header.id}
                      style={{ width: header.column.columnDef.size }}
                      className={`px-3 py-2 text-left font-semibold text-muted-foreground tracking-[0.4px] uppercase select-none ${
                        canSort ? "cursor-pointer hover:text-muted-foreground" : ""
                      }`}
                      onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                    >
                      <div className="flex items-center gap-1">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort && (
                          <span className="text-muted-foreground">
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
                    </TableHead>
                  );
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.map((row, i) => (
              <TableRow
                key={row.id}
                className={`border-b hover:bg-secondary transition-colors ${
                  i % 2 === 0 ? "" : "bg-background"
                }`}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id} className="px-3 py-1.5 text-muted-foreground">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Pagination bar */}
      <div className="flex items-center justify-between px-4 py-2 border-t bg-card shrink-0">
        <span className="text-[11px] text-muted-foreground">
          {from}–{to} / {totalRows} 条记录
        </span>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] text-muted-foreground">每页</span>
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
              className="px-2 py-1 rounded text-[11px] text-muted-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              上一页
            </button>
            <span className="text-[11px] text-muted-foreground px-1">
              {pageIndex + 1} / {table.getPageCount()}
            </span>
            <button
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="px-2 py-1 rounded text-[11px] text-muted-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              下一页
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
