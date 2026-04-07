"use client";

import { useState, useEffect } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { BAR_VISION_TYPES, INTERVAL_OPTIONS, DataTypeInfo } from "./types";

interface BatchFetchDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export function BatchFetchDialog({ open, onClose, onSuccess }: BatchFetchDialogProps) {
  const [symbolsText, setSymbolsText] = useState("");
  const [dataType, setDataType] = useState("");
  const [selectedIntervals, setSelectedIntervals] = useState<string[]>(["1m"]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [assetClass, setAssetClass] = useState("um");

  const [dataTypes, setDataTypes] = useState<DataTypeInfo[]>([]);
  const [typesLoading, setTypesLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Load available data types when dialog opens
  useEffect(() => {
    if (!open) return;
    setTypesLoading(true);
    apiGet<DataTypeInfo[]>("/api/data/types")
      .then((data) => {
        if (data) {
          const implemented = data.filter((d) => d.implemented);
          setDataTypes(implemented);
          if (implemented.length > 0 && !dataType) {
            setDataType(implemented[0].data_type);
          }
        }
      })
      .catch(() => {})
      .finally(() => setTypesLoading(false));
  }, [open]);

  // Reset form when closed
  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => {
        setSymbolsText("");
        setSelectedIntervals(["1m"]);
        setStartDate("");
        setEndDate("");
        setAssetClass("um");
        setError(null);
        setSuccessMsg(null);
        setSubmitting(false);
      }, 200);
      return () => clearTimeout(t);
    }
  }, [open]);

  const needsInterval = BAR_VISION_TYPES.has(dataType);

  function toggleInterval(interval: string) {
    setSelectedIntervals((prev) =>
      prev.includes(interval)
        ? prev.filter((i) => i !== interval)
        : [...prev, interval]
    );
  }

  async function handleSubmit() {
    const symbols = symbolsText
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);

    if (symbols.length === 0) {
      setError("请输入至少一个品种");
      return;
    }
    if (!dataType) {
      setError("请选择数据类型");
      return;
    }
    if (needsInterval && selectedIntervals.length === 0) {
      setError("请至少选择一个周期");
      return;
    }
    if (!startDate || !endDate) {
      setError("请填写日期范围");
      return;
    }

    setSubmitting(true);
    setError(null);
    setSuccessMsg(null);

    try {
      const res = await apiPost<{ count: number }>("/api/data/fetch-batch", {
        symbols,
        intervals: needsInterval ? selectedIntervals : [],
        start: startDate,
        end: endDate,
        data_type: dataType,
        asset_class: assetClass,
      });
      const count = res?.count ?? symbols.length;
      setSuccessMsg(`已提交 ${count} 个任务`);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>批量拉取数据</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          {/* Symbols textarea */}
          <div className="flex flex-col gap-1">
            <label className="qds-stat-label">
              品种列表 (每行一个)
            </label>
            <textarea
              value={symbolsText}
              onChange={(e) => setSymbolsText(e.target.value)}
              placeholder={"BTCUSDT-PERP\nETHUSDT-PERP\nSOLUSDT-PERP"}
              rows={4}
              className="w-full rounded-lg border bg-input px-3 py-2 text-[11px] font-mono text-foreground placeholder:text-muted-foreground resize-none focus:outline-none focus:ring-1 focus:ring-qds-info/50 transition-all"
            />
          </div>

          {/* Data type */}
          <div className="flex flex-col gap-1">
            <label className="qds-stat-label">
              数据类型
            </label>
            {typesLoading ? (
              <div className="h-8 rounded-lg bg-input border flex items-center px-3">
                <span className="text-[10px] text-muted-foreground">加载中...</span>
              </div>
            ) : (
              <Select value={dataType} onValueChange={(v: string | null) => v && setDataType(v)}>
                <SelectTrigger className="h-8 text-sm">
                  <SelectValue placeholder="选择数据类型" />
                </SelectTrigger>
                <SelectContent>
                  {dataTypes.map((dt) => (
                    <SelectItem key={dt.data_type} value={dt.data_type}>
                      {dt.data_type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          {/* Intervals — only for BAR_VISION_TYPES */}
          {needsInterval && (
            <div className="flex flex-col gap-1.5">
              <label className="qds-stat-label">
                周期 (可多选)
              </label>
              <div className="flex flex-wrap gap-1.5">
                {INTERVAL_OPTIONS.map((interval) => {
                  const active = selectedIntervals.includes(interval);
                  return (
                    <button
                      key={interval}
                      type="button"
                      onClick={() => toggleInterval(interval)}
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-[10px] font-bold transition-all ${
                        active
                          ? "bg-qds-info text-white"
                          : "bg-input border text-muted-foreground hover:border-qds-info/50 hover:text-foreground"
                      }`}
                    >
                      {interval}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Date range */}
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="开始日期"
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
            <Input
              label="结束日期"
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </div>

          {/* Asset class */}
          <div className="flex flex-col gap-1">
            <label className="qds-stat-label">
              合约类型
            </label>
            <Select value={assetClass} onValueChange={(v: string | null) => v && setAssetClass(v)}>
              <SelectTrigger className="h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="um">UM (U本位合约)</SelectItem>
                <SelectItem value="cm">CM (币本位合约)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {error && (
            <span className="text-[11px] text-destructive">{error}</span>
          )}
          {successMsg && (
            <span className="text-[11px] text-qds-success">{successMsg}</span>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={onClose}
            className="inline-flex items-center justify-center h-8 rounded-lg border bg-transparent px-4 text-[11px] font-semibold text-muted-foreground hover:bg-input transition-all"
          >
            {successMsg ? "关闭" : "取消"}
          </Button>
          {!successMsg && (
            <Button
              onClick={handleSubmit}
              disabled={submitting}
              className="inline-flex items-center gap-1.5 justify-center h-8 rounded-lg bg-[var(--suc)] text-input px-4 text-[11px] font-bold hover:opacity-90 transition-all disabled:opacity-50"
            >
              {submitting ? (
                <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
              ) : (
                "提交任务"
              )}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
