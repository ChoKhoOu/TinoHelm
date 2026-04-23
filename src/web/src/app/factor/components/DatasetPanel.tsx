"use client";

import { useMemo } from "react";
import { Check, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SectionLabel } from "@/components/qds";

interface DatasetPanelProps {
  universes: string[];
  universe: string;
  onUniverseChange: (v: string) => void;

  /** Symbol subset — empty array = full universe. */
  symbols: string[];
  selectedSymbols: string[];
  onSelectedSymbolsChange: (symbols: string[]) => void;

  startDate: string;
  onStartDateChange: (v: string) => void;
  endDate: string;
  onEndDateChange: (v: string) => void;
  dateError: string | null;
}

/**
 * Dataset configuration panel — universe + (optional) symbol subset + date
 * range.  Mirrors the ``/api/factor/explore`` ``config.universe`` and
 * ``config.start`` / ``config.end`` fields exactly.
 *
 * Design reference: Web UI Kit ``.field`` pattern — sunken input, small
 * mono label above, hint below.
 */
export function DatasetPanel({
  universes,
  universe,
  onUniverseChange,
  symbols,
  selectedSymbols,
  onSelectedSymbolsChange,
  startDate,
  onStartDateChange,
  endDate,
  onEndDateChange,
  dateError,
}: DatasetPanelProps) {
  const hasSubset = selectedSymbols.length > 0;

  const symbolCounter = useMemo(() => {
    if (selectedSymbols.length === 0) return `全部 ${symbols.length} 个`;
    return `${selectedSymbols.length} / ${symbols.length}`;
  }, [selectedSymbols, symbols]);

  const toggleSymbol = (s: string) => {
    if (selectedSymbols.includes(s)) {
      onSelectedSymbolsChange(selectedSymbols.filter((x) => x !== s));
    } else {
      onSelectedSymbolsChange([...selectedSymbols, s]);
    }
  };

  const clearSubset = () => onSelectedSymbolsChange([]);

  return (
    <section className="mb-5">
      <SectionLabel>Universe · 数据集</SectionLabel>

      {/* Universe dropdown */}
      <div className="flex flex-col gap-1 mb-3">
        <Label className="font-mono text-[0.62rem] text-muted-foreground">
          Universe
        </Label>
        <Select
          value={universe}
          onValueChange={(v) => v && onUniverseChange(v)}
        >
          <SelectTrigger className="w-full" data-testid="universe-select">
            <SelectValue placeholder="选择 universe" />
          </SelectTrigger>
          <SelectContent>
            {universes.length === 0 && (
              <SelectItem value="__empty__" disabled>
                未配置 universe
              </SelectItem>
            )}
            {universes.map((u) => (
              <SelectItem key={u} value={u}>
                {u}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Symbol subset chips */}
      {symbols.length > 0 && (
        <div className="flex flex-col gap-1 mb-3">
          <div className="flex items-center justify-between">
            <Label className="font-mono text-[0.62rem] text-muted-foreground">
              Symbols (subset)
            </Label>
            <span className="font-mono text-[0.58rem] text-qds-t3">
              {symbolCounter}
            </span>
          </div>
          <div className="flex flex-wrap gap-1 max-h-[124px] overflow-y-auto bg-input rounded-md p-1.5 border">
            {symbols.map((s) => {
              const active = selectedSymbols.includes(s);
              return (
                <button
                  key={s}
                  type="button"
                  data-testid={`symbol-chip-${s}`}
                  onClick={() => toggleSymbol(s)}
                  className={
                    "font-mono text-[0.62rem] px-1.5 py-0.5 rounded transition-colors cursor-pointer " +
                    (active
                      ? "bg-primary/15 text-primary border border-primary/30"
                      : "bg-card text-muted-foreground border border-transparent hover:bg-secondary hover:text-foreground")
                  }
                >
                  {s}
                </button>
              );
            })}
          </div>
          {hasSubset && (
            <button
              type="button"
              onClick={clearSubset}
              className="self-start font-mono text-[0.58rem] text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            >
              清除子集 · 使用全部 universe
            </button>
          )}
        </div>
      )}

      {/* Date range */}
      <div className="grid grid-cols-2 gap-2 mb-2">
        <div className="flex flex-col gap-1">
          <Label className="font-mono text-[0.62rem] text-muted-foreground">
            开始
          </Label>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => onStartDateChange(e.target.value)}
            data-testid="start-date"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="font-mono text-[0.62rem] text-muted-foreground">
            结束
          </Label>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => onEndDateChange(e.target.value)}
            data-testid="end-date"
          />
        </div>
      </div>

      {/* Date validation + availability hints */}
      {dateError ? (
        <div className="font-mono text-[0.62rem] text-destructive bg-input px-2 py-1.5 rounded flex items-center gap-1 mt-1">
          <X className="w-3 h-3" />
          {dateError}
        </div>
      ) : (
        <div className="font-mono text-[0.62rem] text-qds-success bg-qds-success-dim px-2 py-1.5 rounded flex items-center gap-1 mt-1">
          <Check className="w-3 h-3" />
          日期已就绪 · {startDate} → {endDate}
        </div>
      )}
    </section>
  );
}
