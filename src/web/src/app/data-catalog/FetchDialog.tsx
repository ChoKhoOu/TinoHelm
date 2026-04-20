"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Download } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import {
  Dialog,
  DialogContent,
} from "@/components/ui/dialog";
import {
  BAR_VISION_TYPES,
  INTERVAL_OPTIONS,
  DataTypeInfo,
} from "./types";

interface FetchDialogProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

type SymbolInfo = { symbol: string; base: string; quote: string };

export function FetchDialog({ open, onClose, onSuccess }: FetchDialogProps) {
  // Multi-symbol selection
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [symbolSearch, setSymbolSearch] = useState("");
  const [symbolDropdownOpen, setSymbolDropdownOpen] = useState(false);
  const [allSymbols, setAllSymbols] = useState<SymbolInfo[]>([]);

  const [dataType, setDataType] = useState("klines");
  const [dataTypes, setDataTypes] = useState<DataTypeInfo[]>([]);
  const [assetClass, setAssetClass] = useState("um");
  const [interval, setInterval] = useState("1m");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  // Progress tracking
  const [phase, setPhase] = useState<"form" | "progress">("form");
  const [jobCount, setJobCount] = useState(0);
  const [progressMsg, setProgressMsg] = useState("已提交，等待开始...");

  const onSuccessRef = useRef(onSuccess);
  onSuccessRef.current = onSuccess;

  const chipInputRef = useRef<HTMLInputElement>(null);

  // Store interval/showInterval in refs so the apiFn closure stays stable
  const intervalRef = useRef(interval);
  intervalRef.current = interval;
  const dataTypeRef = useRef(dataType);
  dataTypeRef.current = dataType;
  const assetClassRef = useRef(assetClass);
  assetClassRef.current = assetClass;
  const startDateRef = useRef(startDate);
  startDateRef.current = startDate;
  const endDateRef = useRef(endDate);
  endDateRef.current = endDate;
  const selectedSymbolsRef = useRef(selectedSymbols);
  selectedSymbolsRef.current = selectedSymbols;

  const showInterval = BAR_VISION_TYPES.has(dataType);

  const apiFn = useCallback(async () => {
    const syms = selectedSymbolsRef.current;
    const dt = dataTypeRef.current;
    const showInt = BAR_VISION_TYPES.has(dt);
    const body: Record<string, unknown> = {
      symbols: syms,
      intervals: showInt ? [intervalRef.current] : ["1m"],
      data_type: dt,
      asset_class: assetClassRef.current,
      start: startDateRef.current,
      end: endDateRef.current,
    };
    return apiPost<{ count: number }>("/api/data/fetch-batch", body);
  }, []);

  const { state: actionState, error: actionError, execute } = useAction(apiFn, {
    successDuration: 1000,
    onSuccess: (result) => {
      const count = result?.count ?? selectedSymbolsRef.current.length;
      setJobCount(count);
      setProgressMsg(`已提交 ${selectedSymbolsRef.current.length} 个品种的拉取任务`);
      onSuccessRef.current();
      setTimeout(() => {
        setPhase("progress");
      }, 0);
      setTimeout(() => {
        onClose();
      }, 1000);
    },
  });

  // Fetch available data types and symbols on mount
  useEffect(() => {
    apiGet<DataTypeInfo[]>("/api/data/types")
      .then((res) => {
        if (res) setDataTypes(res.filter((t) => t.implemented));
      })
      .catch(() => {});
    apiGet<SymbolInfo[]>("/api/data/symbols")
      .then((res) => {
        if (res) setAllSymbols(res);
      })
      .catch(() => {});
  }, []);

  const filteredSymbols = useMemo(() => {
    const q = symbolSearch.toUpperCase();
    const available = allSymbols.filter(
      (s) => !selectedSymbols.includes(s.symbol),
    );
    if (!q) return available.slice(0, 50);
    return available.filter(
      (s) => s.symbol.includes(q) || s.base.includes(q),
    ).slice(0, 50);
  }, [symbolSearch, allSymbols, selectedSymbols]);

  function addSymbol(sym: string) {
    if (!selectedSymbols.includes(sym)) {
      setSelectedSymbols((prev) => [...prev, sym]);
    }
    setSymbolSearch("");
    setSymbolDropdownOpen(false);
  }

  function removeSymbol(sym: string) {
    setSelectedSymbols((prev) => prev.filter((s) => s !== sym));
  }

  // Reset on dialog close
  useEffect(() => {
    if (!open) {
      const t = setTimeout(() => {
        setPhase("form");
        setValidationError(null);
        setJobCount(0);
        setProgressMsg("已提交，等待开始...");
      }, 200);
      return () => clearTimeout(t);
    }
  }, [open]);

  function validateDates(): string | null {
    if (!startDate || !endDate) return "请填写开始和结束日期";
    const s = new Date(startDate);
    const e = new Date(endDate);
    if (isNaN(s.getTime()) || isNaN(e.getTime())) return "日期格式无效";
    if (s > e) return "开始日期不能晚于结束日期";
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (e > today) return "结束日期不能超过今天";
    // Max 2 years range
    const diffDays = (e.getTime() - s.getTime()) / (1000 * 60 * 60 * 24);
    if (diffDays > 730) return "日期范围不能超过 2 年";
    return null;
  }

  function handleSubmit() {
    if (selectedSymbols.length === 0) {
      setValidationError("请至少选择一个品种");
      return;
    }
    const dateErr = validateDates();
    if (dateErr) {
      setValidationError(dateErr);
      return;
    }
    setValidationError(null);
    execute();
  }

  const displayError = validationError ?? actionError;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border sm:max-w-[520px] p-0 overflow-hidden">
        {phase === "form" ? (
          <>
            {/* Modal header */}
            <div style={{ padding: "1rem 1.25rem .75rem", display: "flex", alignItems: "flex-start", gap: ".75rem" }}>
              <div
                className="w-9 h-9 rounded-[10px] flex items-center justify-center flex-shrink-0 text-base"
                style={{ background: "var(--suc-d)", color: "var(--suc)" }}
              >↓</div>
              <div>
                <div className="text-[.9rem] font-semibold mb-[.15rem]">拉取数据</div>
                <div className="text-[.75rem] text-muted-foreground leading-relaxed">从 Binance 拉取历史数据到本地 ParquetDataCatalog</div>
              </div>
            </div>

            {/* Modal body */}
            <div style={{ padding: "0 1.25rem 1rem" }}>
              {/* Data type + Asset class — 2-column row */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: ".75rem", marginBottom: ".85rem" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                  <div className="font-mono text-[.62rem] text-muted-foreground">数据类型 <span className="text-destructive">*</span></div>
                  <select
                    className="qds-select"
                    value={dataType}
                    onChange={(e) => setDataType(e.target.value)}
                  >
                    {dataTypes.length > 0 ? (
                      dataTypes.map((t) => (
                        <option key={t.data_type} value={t.data_type}>{t.data_type}</option>
                      ))
                    ) : (
                      <option value="klines">klines</option>
                    )}
                  </select>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                  <div className="font-mono text-[.62rem] text-muted-foreground">资产类别</div>
                  <select
                    className="qds-select"
                    value={assetClass}
                    onChange={(e) => setAssetClass(e.target.value)}
                  >
                    <option value="um">U 本位 (USDT-M)</option>
                    <option value="cm">币本位 (COIN-M)</option>
                  </select>
                </div>
              </div>

              {/* Interval — only for bar types */}
              {showInterval && (
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem", marginBottom: ".85rem" }}>
                  <div className="font-mono text-[.62rem] text-muted-foreground">周期 <span className="text-destructive">*</span></div>
                  <select
                    className="qds-select"
                    value={interval}
                    onChange={(e) => setInterval(e.target.value)}
                  >
                    {INTERVAL_OPTIONS.map((opt) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Symbol — chip multi-select */}
              <div style={{ display: "flex", flexDirection: "column", gap: ".3rem", marginBottom: ".85rem" }}>
                <div className="font-mono text-[.62rem] text-muted-foreground">
                  品种 <span className="text-destructive">*</span>
                  <span className="font-normal text-qds-t3">· 可多选</span>
                </div>
                <div style={{ position: "relative" }}>
                  <div
                    className="flex flex-wrap gap-1.5 p-[.35rem_.5rem] min-h-9 bg-input border border-border rounded-[var(--rs)] cursor-text transition-colors duration-200 focus-within:border-primary focus-within:shadow-[0_0_0_3px_var(--acc-d)]"
                    onClick={() => chipInputRef.current?.focus()}
                  >
                    {selectedSymbols.map((sym) => (
                      <span key={sym} className="inline-flex items-center gap-1 font-mono text-[.68rem] px-[.4rem] py-[.15rem] bg-secondary rounded text-foreground whitespace-nowrap">
                        {sym}
                        <span
                          className="cursor-pointer text-qds-t3 text-[.6rem] hover:text-destructive transition-colors duration-150"
                          onClick={(e) => { e.stopPropagation(); removeSymbol(sym); }}
                        >×</span>
                      </span>
                    ))}
                    <input
                      ref={chipInputRef}
                      className="border-0 bg-transparent outline-none font-mono text-[.68rem] text-foreground flex-1 min-w-20 py-[.1rem] placeholder:text-qds-t3"
                      placeholder="搜索品种..."
                      value={symbolSearch}
                      onChange={(e) => {
                        setSymbolSearch(e.target.value);
                        setSymbolDropdownOpen(true);
                      }}
                      onFocus={() => setSymbolDropdownOpen(true)}
                      onBlur={() => setTimeout(() => setSymbolDropdownOpen(false), 150)}
                    />
                  </div>
                  {symbolDropdownOpen && filteredSymbols.length > 0 && (
                    <div className="absolute top-full left-0 right-0 bg-card border border-border rounded-[var(--rs)] max-h-[180px] overflow-y-auto z-10 shadow-[0_8px_24px_rgba(0,0,0,.2)]">
                      {filteredSymbols.map((s) => (
                        <div
                          key={s.symbol}
                          className="px-[.65rem] py-[.4rem] font-mono text-[.7rem] cursor-pointer hover:bg-secondary transition-colors duration-150"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            addSymbol(s.symbol);
                          }}
                        >
                          <span className="font-mono">{s.symbol}</span>
                          <span className="text-[.62rem] text-qds-t3 ml-2">{s.base}/{s.quote}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Date range — 2-column row */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: ".75rem", marginBottom: ".85rem" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                  <div className="font-mono text-[.62rem] text-muted-foreground">开始日期 <span className="text-destructive">*</span></div>
                  <input
                    className="qds-input"
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                  <div className="font-mono text-[.62rem] text-muted-foreground">结束日期 <span className="text-destructive">*</span></div>
                  <input
                    className="qds-input"
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                  />
                </div>
              </div>

              {displayError && (
                <div className="text-[.62rem] text-destructive mt-[.1rem]">{displayError}</div>
              )}
            </div>

            {/* Footer */}
            <div style={{ padding: ".75rem 1.25rem", borderTop: "1px solid var(--bd)", display: "flex", justifyContent: "flex-end", gap: ".5rem" }}>
              <button
                className="btn btn-o"
                onClick={onClose}
              >
                取消
              </button>
              <button
                className={actionState === "error" ? "btn btn-d" : "btn btn-p"}
                onClick={handleSubmit}
                disabled={actionState === "loading"}
              >
                {actionState === "loading" ? (
                  <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
                ) : actionState === "success" ? (
                  <span>✓</span>
                ) : actionState === "error" ? (
                  <span>✕</span>
                ) : (
                  <Download style={{ width: "12px", height: "12px" }} />
                )}
                {actionState === "loading"
                  ? "提交中..."
                  : actionState === "success"
                  ? `✓ ${jobCount > 0 ? jobCount + " 个" : ""}已入队`
                  : actionState === "error"
                  ? "✕ 失败"
                  : selectedSymbols.length > 1
                  ? `拉取 ${selectedSymbols.length} 个品种`
                  : "提交拉取"}
              </button>
            </div>
          </>
        ) : (
          <>
            {/* Progress phase header */}
            <div style={{ padding: "1rem 1.25rem .75rem", display: "flex", alignItems: "flex-start", gap: ".75rem" }}>
              <div
                className="w-9 h-9 rounded-[10px] flex items-center justify-center flex-shrink-0 text-base"
                style={{ background: "var(--suc-d)", color: "var(--suc)" }}
              >✓</div>
              <div>
                <div className="text-[.9rem] font-semibold mb-[.15rem] text-qds-success">任务已提交</div>
                <div className="text-[.75rem] text-muted-foreground leading-relaxed">拉取任务已加入队列，可在后台运行</div>
              </div>
            </div>

            <div style={{ padding: "0 1.25rem 1rem" }}>
              <p className="text-[.8rem] text-qds-t1 mb-2">{progressMsg}</p>
              <p className="font-mono text-[.68rem] text-qds-t3">
                共 {jobCount} 个拉取任务已加入队列，可在后台运行。关闭此对话框不影响任务执行。
              </p>
            </div>

            <div style={{ padding: ".75rem 1.25rem", borderTop: "1px solid var(--bd)", display: "flex", justifyContent: "flex-end" }}>
              <button className="btn btn-p" onClick={onClose}>完成</button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
