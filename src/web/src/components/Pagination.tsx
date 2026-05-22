"use client";

import { useMemo } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationProps {
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  pageSizeOptions?: number[];
  className?: string;
}

function getPageNumbers(current: number, total: number): (number | "ellipsis")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);

  const pages: (number | "ellipsis")[] = [1];

  if (current > 3) pages.push("ellipsis");

  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);

  for (let i = start; i <= end; i++) pages.push(i);

  if (current < total - 2) pages.push("ellipsis");

  pages.push(total);
  return pages;
}

export function Pagination({
  total,
  page,
  pageSize,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [20, 50, 100],
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeStart = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const rangeEnd = Math.min(page * pageSize, total);

  const pages = useMemo(() => getPageNumbers(page, totalPages), [page, totalPages]);

  return (
    <div className={cn("flex items-center justify-between gap-4", className)}>
      <span className="text-[0.72rem] text-muted-foreground whitespace-nowrap">
        显示 {rangeStart}-{rangeEnd} / 共 {total} 条
      </span>

      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon-xs"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft />
        </Button>

        {pages.map((p, i) =>
          p === "ellipsis" ? (
            <span
              key={`e${i}`}
              className="flex size-6 items-center justify-center text-xs text-qds-t3"
            >
              ...
            </span>
          ) : (
            <Button
              key={p}
              variant={p === page ? "secondary" : "ghost"}
              size="icon-xs"
              onClick={() => onPageChange(p)}
              className="text-xs"
            >
              {p}
            </Button>
          ),
        )}

        <Button
          variant="ghost"
          size="icon-xs"
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight />
        </Button>
      </div>

      <Select
        value={pageSize}
        onValueChange={(val) => onPageSizeChange(Number(val))}
      >
        <SelectTrigger size="sm" className="w-auto gap-1 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent align="end">
          {pageSizeOptions.map((opt) => (
            <SelectItem key={opt} value={opt}>
              {opt} 条/页
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
