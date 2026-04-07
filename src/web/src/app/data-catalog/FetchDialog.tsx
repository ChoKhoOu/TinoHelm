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
            <div className="modal-head" style={{ padding: "1rem 1.25rem .75rem", display: "flex", alignItems: "flex-start", gap: ".75rem" }}>
              <div className="dc-modal-icon" style={{ background: "var(--suc-d)", color: "var(--suc)" }}>↓</div>
              <div>
                <div style={{ fontSize: ".9rem", fontWeight: 600, marginBottom: ".15rem" }}>拉取数据</div>
                <div style={{ fontSize: ".75rem", color: "var(--t2)", lineHeight: 1.5 }}>从 Binance 拉取历史数据到本地 ParquetDataCatalog</div>
              </div>
            </div>

            {/* Modal body */}
            <div style={{ padding: "0 1.25rem 1rem" }}>
              {/* Data type + Asset class — 2-column row */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: ".75rem", marginBottom: ".85rem" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                  <div className="fl">数据类型 <span className="req" style={{ color: "var(--dan)" }}>*</span></div>
                  <select
                    className="fsel"
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
                  <div className="fl">资产类别</div>
                  <select
                    className="fsel"
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
                  <div className="fl">周期 <span className="req" style={{ color: "var(--dan)" }}>*</span></div>
                  <select
                    className="fsel"
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
                <div className="fl">
                  品种 <span className="req" style={{ color: "var(--dan)" }}>*</span>
                  <span style={{ fontWeight: 400, color: "var(--t3)" }}>· 可多选</span>
                </div>
                <div style={{ position: "relative" }}>
                  <div
                    className="dc-chip-wrap"
                    onClick={() => chipInputRef.current?.focus()}
                  >
                    {selectedSymbols.map((sym) => (
                      <span key={sym} className="dc-chip">
                        {sym}
                        <span
                          className="dc-chip-x"
                          onClick={(e) => { e.stopPropagation(); removeSymbol(sym); }}
                        >×</span>
                      </span>
                    ))}
                    <input
                      ref={chipInputRef}
                      className="dc-chip-input"
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
                    <div className="dc-chip-dropdown" style={{ display: "block" }}>
                      {filteredSymbols.map((s) => (
                        <div
                          key={s.symbol}
                          className="dc-chip-opt"
                          onMouseDown={(e) => {
                            e.preventDefault();
                            addSymbol(s.symbol);
                          }}
                        >
                          <span style={{ fontFamily: "var(--font-d)" }}>{s.symbol}</span>
                          <span style={{ fontSize: ".62rem", color: "var(--t3)", marginLeft: ".5rem" }}>{s.base}/{s.quote}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Date range — 2-column row */}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: ".75rem", marginBottom: ".85rem" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                  <div className="fl">开始日期 <span className="req" style={{ color: "var(--dan)" }}>*</span></div>
                  <input
                    className="fi"
                    type="date"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                  <div className="fl">结束日期 <span className="req" style={{ color: "var(--dan)" }}>*</span></div>
                  <input
                    className="fi"
                    type="date"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                  />
                </div>
              </div>

              {displayError && (
                <div style={{ fontSize: ".62rem", color: "var(--dan)", marginTop: ".1rem" }}>{displayError}</div>
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
              <div className="dc-modal-icon" style={{ background: "var(--suc-d)", color: "var(--suc)" }}>✓</div>
              <div>
                <div style={{ fontSize: ".9rem", fontWeight: 600, marginBottom: ".15rem", color: "var(--suc)" }}>任务已提交</div>
                <div style={{ fontSize: ".75rem", color: "var(--t2)", lineHeight: 1.5 }}>拉取任务已加入队列，可在后台运行</div>
              </div>
            </div>

            <div style={{ padding: "0 1.25rem 1rem" }}>
              <p style={{ fontSize: ".8rem", color: "var(--t1)", marginBottom: ".5rem" }}>{progressMsg}</p>
              <p style={{ fontSize: ".68rem", color: "var(--t3)", fontFamily: "var(--font-d)" }}>
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
