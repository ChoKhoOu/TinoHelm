"use client";

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
import { INTERVAL_OPTIONS } from "@/app/data-catalog/types";
import type { SymbolOption } from "./types";

interface DataAvailability {
  total: number;
  minDate: string;
  maxDate: string;
}

interface ResearchDatasetPanelProps {
  symbols: SymbolOption[];
  symbol: string;
  onSymbolChange: (v: string) => void;
  dataType: string;
  onDataTypeChange: (v: string) => void;
  interval: string;
  onIntervalChange: (v: string) => void;
  tickSource: "aggTrades" | "trades";
  onTickSourceChange: (v: "aggTrades" | "trades") => void;
  startDate: string;
  onStartDateChange: (v: string) => void;
  endDate: string;
  onEndDateChange: (v: string) => void;
  dataAvail: DataAvailability | null;
  dateError: string | null;
}

export function ResearchDatasetPanel({
  symbols,
  symbol,
  onSymbolChange,
  dataType,
  onDataTypeChange,
  interval,
  onIntervalChange,
  tickSource,
  onTickSourceChange,
  startDate,
  onStartDateChange,
  endDate,
  onEndDateChange,
  dataAvail,
  dateError,
}: ResearchDatasetPanelProps) {
  return (
    <section className="mb-5">
      <SectionLabel>品种与数据</SectionLabel>

      {/* Symbol */}
      <div className="flex flex-col gap-1 mb-3">
        <Label className="font-mono text-[0.62rem] text-muted-foreground">品种</Label>
        <Select value={symbol} onValueChange={(v) => v && onSymbolChange(v)}>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {symbols.map((s) => (
              <SelectItem key={s.symbol} value={s.symbol}>
                {s.label ?? s.symbol}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Data type + interval/tickSource */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="flex flex-col gap-1">
          <Label className="font-mono text-[0.62rem] text-muted-foreground">数据类型</Label>
          <Select value={dataType} onValueChange={(v) => v && onDataTypeChange(v)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="bar">bar</SelectItem>
              <SelectItem value="trade_tick">trade_tick</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <Label className="font-mono text-[0.62rem] text-muted-foreground">
            {dataType === "trade_tick" ? "数据源" : "粒度"}
          </Label>
          {dataType === "trade_tick" ? (
            <Select
              value={tickSource}
              onValueChange={(v) => v && onTickSourceChange(v as "aggTrades" | "trades")}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="aggTrades">aggTrades</SelectItem>
                <SelectItem value="trades">trades</SelectItem>
              </SelectContent>
            </Select>
          ) : (
            <Select value={interval} onValueChange={(v) => v && onIntervalChange(v)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {INTERVAL_OPTIONS.map((opt) => (
                  <SelectItem key={opt} value={opt}>
                    {opt}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>
      </div>

      {/* Date range */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div className="flex flex-col gap-1">
          <Label className="font-mono text-[0.62rem] text-muted-foreground">开始</Label>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => onStartDateChange(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label className="font-mono text-[0.62rem] text-muted-foreground">结束</Label>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => onEndDateChange(e.target.value)}
          />
        </div>
      </div>

      {/* Availability indicator */}
      {dataAvail ? (
        <div className="font-mono text-[0.62rem] text-qds-success bg-qds-success-dim px-2 py-1.5 rounded flex items-center gap-1">
          <Check className="w-3 h-3" />
          可用:{" "}
          {dataAvail.total >= 1_000_000
            ? `${(dataAvail.total / 1_000_000).toFixed(1)}M`
            : dataAvail.total >= 1_000
            ? `${(dataAvail.total / 1_000).toFixed(0)}K`
            : dataAvail.total}{" "}
          {dataType === "bar" ? "bars" : dataType === "trade_tick" ? "ticks" : dataType}
          <span className="ml-1 text-qds-t3">
            ({dataAvail.minDate} ~ {dataAvail.maxDate})
          </span>
        </div>
      ) : (
        <div className="font-mono text-[0.62rem] text-qds-t3 bg-input px-2 py-1.5 rounded flex items-center gap-1 mt-1">
          <X className="w-3 h-3" />
          无本地数据
        </div>
      )}

      {dateError && (
        <div className="font-mono text-[0.62rem] text-destructive bg-input px-2 py-1.5 rounded flex items-center gap-1 mt-1">
          <X className="w-3 h-3" />
          {dateError}
        </div>
      )}
    </section>
  );
}
