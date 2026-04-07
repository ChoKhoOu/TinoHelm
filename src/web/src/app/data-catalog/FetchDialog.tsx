"use client";

import { useState, useEffect, useRef } from "react";
import { Download } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { useWsEvent } from "@/providers/WebSocketProvider";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  BAR_VISION_TYPES,
  INTERVAL_OPTIONS,
  DataTypeInfo,
  DataProgressPayload,
} from "./types";

interface FetchDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export function FetchDialog({ open, onClose, onSuccess }: FetchDialogProps) {
  const [symbol, setSymbol] = useState("");
  const [dataType, setDataType] = useState("klines");
  const [dataTypes, setDataTypes] = useState<DataTypeInfo[]>([]);
  const [assetClass, setAssetClass] = useState("um");
  const [interval, setInterval] = useState("1m");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Progress tracking
  const [phase, setPhase] = useState<"form" | "progress">("form");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);
  const [progressMsg, setProgressMsg] = useState("已提交，等待开始...");

  const progressEvent = useWsEvent("data.progress");
  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;
  const refreshedRef = useRef(false);

  const showInterval = BAR_VISION_TYPES.has(dataType);

  // Fetch available data types on mount
  useEffect(() => {
    apiGet<DataTypeInfo[]>("/api/data/types")
      .then((res) => {
        if (res) {
          setDataTypes(res.filter((t) => t.implemented));
        }
      })
      .catch(() => {
        // Silently ignore — form still works with the default "klines"
      });
  }, []);

  // Track progress from WS events
  useEffect(() => {
    if (!progressEvent || !taskId || phase !== "progress") return;
    const evt = progressEvent as unknown as DataProgressPayload;
    if (evt.task_id !== taskId) return;

    setProgress(evt.progress);
    setProgressMsg(evt.message);

    if (evt.progress === 100 && !refreshedRef.current) {
      refreshedRef.current = true;
      onSuccessRef.current();
    }
  }, [progressEvent, taskId, phase]);

  // Reset on dialog close
  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => {
        setPhase("form");
        setProgress(0);
        setProgressMsg("已提交，等待开始...");
        setError(null);
        setTaskId(null);
        setSubmitting(false);
        refreshedRef.current = false;
      }, 200);
      return () => clearTimeout(t);
    }
  }, [open]);

  async function handleSubmit() {
    if (!symbol.trim() || !startDate || !endDate) {
      setError("请填写所有必填项");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const body: Record<string, string> = {
        symbol: symbol.trim(),
        data_type: dataType,
        asset_class: assetClass,
        start: startDate,
        end: endDate,
      };
      if (showInterval) {
        body.interval = interval;
      }
      const res = await apiPost<{ task_id: string }>("/api/data/fetch", body);
      setTaskId(res?.task_id ?? null);
      setPhase("progress");
      setProgress(0);
      setProgressMsg("已提交，等待开始...");
      refreshedRef.current = false;
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  const isDone = progress === 100;
  const isError = progress === -1;

  // Human-readable label for data type in progress view
  const dataTypeLabel = dataTypes.find((t) => t.data_type === dataType)?.data_type ?? dataType;
  const progressSubtitle = showInterval
    ? `${symbol.trim()} · ${interval}`
    : `${symbol.trim()} · ${dataTypeLabel}`;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border sm:max-w-[480px]">
        {phase === "form" ? (
          <>
            <DialogHeader>
              <DialogTitle>拉取数据</DialogTitle>
            </DialogHeader>
            <div className="flex flex-col gap-4 py-2">
              {/* Data type */}
              <div className="flex flex-col gap-1">
                <label className="qds-stat-label">
                  数据类型
                </label>
                <Select value={dataType} onValueChange={(v: string | null) => v && setDataType(v)}>
                  <SelectTrigger className="h-8 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {dataTypes.length > 0 ? (
                      dataTypes.map((t) => (
                        <SelectItem key={t.data_type} value={t.data_type}>
                          {t.data_type}
                        </SelectItem>
                      ))
                    ) : (
                      /* Fallback while loading */
                      <SelectItem value="klines">klines</SelectItem>
                    )}
                  </SelectContent>
                </Select>
              </div>

              {/* Asset class */}
              <div className="flex flex-col gap-1">
                <label className="qds-stat-label">
                  资产类别
                </label>
                <Select value={assetClass} onValueChange={(v: string | null) => v && setAssetClass(v)}>
                  <SelectTrigger className="h-8 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="um">um (U本位合约)</SelectItem>
                    <SelectItem value="cm">cm (币本位合约)</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Symbol */}
              <Input
                label="品种 (如 BTCUSDT-PERP)"
                placeholder="BTCUSDT-PERP"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              />

              {/* Interval — only for bar types */}
              {showInterval && (
                <div className="flex flex-col gap-1">
                  <label className="qds-stat-label">
                    周期
                  </label>
                  <Select value={interval} onValueChange={(v: string | null) => v && setInterval(v)}>
                    <SelectTrigger className="h-8 text-sm">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {INTERVAL_OPTIONS.map((opt) => (
                        <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
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

              {error && (
                <span className="text-[11px] text-destructive">{error}</span>
              )}
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={onClose}
                className="inline-flex items-center justify-center h-8 rounded-lg border bg-transparent px-4 text-[11px] font-semibold text-muted-foreground hover:bg-input transition-all"
              >
                取消
              </Button>
              <Button
                onClick={handleSubmit}
                disabled={submitting}
                className="inline-flex items-center gap-1.5 justify-center h-8 rounded-lg bg-[var(--suc)] text-input px-4 text-[11px] font-bold hover:opacity-90 transition-all disabled:opacity-50"
              >
                {submitting ? (
                  <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
                ) : (
                  <Download className="w-3 h-3" />
                )}
                开始拉取
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                {isDone ? (
                  <span className="text-qds-success">拉取完成</span>
                ) : isError ? (
                  <span className="text-destructive">拉取失败</span>
                ) : (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-qds-info border-t-transparent rounded-full animate-spin" />
                    正在拉取数据
                  </>
                )}
              </DialogTitle>
            </DialogHeader>
            <div className="py-4 flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground font-mono">
                  {progressSubtitle}
                </span>
                <span className={`text-xs font-mono font-bold ${
                  isDone ? "text-qds-success" : isError ? "text-destructive" : "text-foreground"
                }`}>
                  {isError ? "错误" : `${Math.max(0, progress)}%`}
                </span>
              </div>
              {/* Progress bar */}
              <div className="h-1.5 rounded-full bg-secondary overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-700 ease-out ${
                    isError
                      ? "bg-[var(--dan)]"
                      : isDone
                        ? "bg-[var(--suc)]"
                        : "bg-qds-info"
                  }`}
                  style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
                />
              </div>
              <p className="text-[11px] text-muted-foreground leading-relaxed">
                {progressMsg}
              </p>
            </div>
            <DialogFooter>
              {isDone ? (
                <Button
                  onClick={onClose}
                  className="inline-flex items-center gap-1.5 justify-center h-8 rounded-lg bg-[var(--suc)] text-input px-4 text-[11px] font-bold hover:opacity-90 transition-all"
                >
                  完成
                </Button>
              ) : (
                <Button
                  variant="outline"
                  onClick={onClose}
                  className="inline-flex items-center justify-center h-8 rounded-lg border bg-transparent px-4 text-[11px] font-semibold text-muted-foreground hover:bg-input transition-all"
                >
                  {isError ? "关闭" : "后台运行"}
                </Button>
              )}
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
