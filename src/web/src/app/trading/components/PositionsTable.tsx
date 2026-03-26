"use client";

import { useState, useEffect, useRef } from "react";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type ColumnDef,
} from "@tanstack/react-table";
import { TrendingUp, TrendingDown, Inbox } from "lucide-react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";

export interface Position {
  position_id: string;
  instrument_id: string;
  side: "LONG" | "SHORT";
  quantity: string;
  avg_px_open: string;
  unrealized_pnl: string;
  unrealized_pnl_value: number;
  duration: string;
}

interface Props {
  positions: Position[];
}

const columnHelper = createColumnHelper<Position>();


export function PositionsTable({ positions }: Props) {
  // Track which rows were recently updated for flash animation
  const [flashMap, setFlashMap] = useState<Record<string, "positive" | "negative">>({});
  const prevPositionsRef = useRef<Map<string, Position>>(new Map());

  useEffect(() => {
    const newFlash: Record<string, "positive" | "negative"> = {};
    const prevMap = prevPositionsRef.current;

    positions.forEach((pos) => {
      const prev = prevMap.get(pos.position_id);
      if (prev && prev.unrealized_pnl_value !== pos.unrealized_pnl_value) {
        newFlash[pos.position_id] =
          pos.unrealized_pnl_value >= prev.unrealized_pnl_value ? "positive" : "negative";
      }
    });

    // Always update previous positions map
    const newMap = new Map<string, Position>();
    positions.forEach((p) => newMap.set(p.position_id, p));
    prevPositionsRef.current = newMap;

    if (Object.keys(newFlash).length > 0) {
      setFlashMap(newFlash);
      const timer = setTimeout(() => setFlashMap({}), 350);
      return () => clearTimeout(timer);
    }
  }, [positions]);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const columns: ColumnDef<Position, any>[] = [
    columnHelper.accessor("instrument_id", {
      header: "标的",
      cell: (info) => (
        <span className="text-[11px] font-mono font-semibold text-foreground">
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.accessor("side", {
      header: "方向",
      cell: (info) => {
        const side = info.getValue();
        const isLong = side === "LONG";
        return (
          <span
            className="inline-flex items-center gap-1 text-[11px] font-bold"
            style={{ color: isLong ? "var(--accent-green)" : "var(--accent-red)" }}
          >
            {isLong ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
            {isLong ? "多" : "空"}
          </span>
        );
      },
    }),
    columnHelper.accessor("quantity", {
      header: "数量",
      cell: (info) => (
        <span className="text-[11px] font-mono text-muted-foreground">
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.accessor("avg_px_open", {
      header: "开仓均价",
      cell: (info) => (
        <span className="text-[11px] font-mono text-muted-foreground">
          {info.getValue()}
        </span>
      ),
    }),
    columnHelper.accessor("unrealized_pnl", {
      header: "未实现盈亏",
      cell: (info) => {
        const value = info.row.original.unrealized_pnl_value;
        const isPos = value >= 0;
        return (
          <span
            className="text-[11px] font-mono font-semibold"
            style={{ color: isPos ? "var(--accent-green)" : "var(--accent-red)" }}
          >
            {isPos ? "+" : ""}{info.getValue()}
          </span>
        );
      },
    }),
    columnHelper.accessor("duration", {
      header: "持仓时长",
      cell: (info) => (
        <span className="text-[11px] font-mono text-muted-foreground">
          {info.getValue() || "—"}
        </span>
      ),
    }),
  ];

  const table = useReactTable({
    data: positions,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row) => row.position_id,
  });

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
            持仓
          </span>
          <span
            className="px-1.5 py-0.5 rounded text-[9px] font-bold"
            style={{
              color: "var(--accent-blue)",
              backgroundColor: "var(--accent-blue-20)",
            }}
          >
            {positions.length}
          </span>
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {positions.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-muted-foreground">
            <Inbox className="w-8 h-8 opacity-30" />
            <span className="text-[12px]">暂无持仓</span>
          </div>
        ) : (
          <Table className="w-full text-left border-collapse">
            <TableHeader className="sticky top-0 bg-card z-10">
              {table.getHeaderGroups().map((headerGroup) => (
                <TableRow key={headerGroup.id}>
                  {headerGroup.headers.map((header) => (
                    <TableHead
                      key={header.id}
                      className="px-4 py-2 text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase border-b border-border whitespace-nowrap"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                    </TableHead>
                  ))}
                </TableRow>
              ))}
            </TableHeader>
            <TableBody>
              {table.getRowModel().rows.map((row) => {
                const flash = flashMap[row.id];
                return (
                  <TableRow
                    key={row.id}
                    className={`border-b border-border last:border-b-0 transition-colors hover:bg-popover ${
                      flash === "positive"
                        ? "flash-positive"
                        : flash === "negative"
                        ? "flash-negative"
                        : ""
                    }`}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <TableCell key={cell.id} className="px-4 py-2.5 whitespace-nowrap">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </TableCell>
                    ))}
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
