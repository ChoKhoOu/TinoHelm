"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Play, Send, ArrowRight } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { InlineError } from "@/components/qds";
import {
  CreateFactorDialog,
  ResearchDatasetPanel,
  ResearchExploreResult,
  ResearchFactorList,
  ResearchJobQueue,
  ResearchParams,
} from "./components";
import {
  FALLBACK_FACTOR_GROUPS,
  FALLBACK_SYMBOLS,
  MAX_FACTORS,
  type CatalogEntry,
  type ExploreResult,
  type FactorGroup,
  type HistoryJob,
  type SymbolOption,
} from "./components/types";

/* ================================================================== */
/*  Page component                                                     */
/* ================================================================== */

export default function ResearchPage() {
  /* -- API data ---------------------------------------------------- */
  const [symbols, setSymbols] = useState<SymbolOption[]>(FALLBACK_SYMBOLS);
  const [factorGroups, setFactorGroups] = useState<FactorGroup[]>(FALLBACK_FACTOR_GROUPS);
  const [histJobs, setHistJobs] = useState<HistoryJob[]>([]);
  const [histLoading, setHistLoading] = useState(true);

  /* -- Config state ------------------------------------------------ */
  const [symbol, setSymbol] = useState("BTCUSDT-PERP");
  const [dataType, setDataType] = useState("bar");
  const [interval, setInterval] = useState("5m");
  const [tickSource, setTickSource] = useState<"aggTrades" | "trades">("aggTrades");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-04-01");
  const [selectedFactors, setSelectedFactors] = useState<Set<string>>(
    new Set(["ret_N", "vol_ratio", "volume_surge"]),
  );

  /* -- Accordion state --------------------------------------------- */
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set(["动量"]));
  const toggleGroup = useCallback((g: string) => {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });
  }, []);

  /* -- Params state ------------------------------------------------ */
  const [forwardPeriod, setForwardPeriod] = useState(5);
  const [quantiles, setQuantiles] = useState(5);
  const [returnType, setReturnType] = useState("simple");
  const [factorParams, setFactorParams] = useState<Record<string, Record<string, number>>>({});

  /* -- Result state ------------------------------------------------ */
  const [result, setResult] = useState<ExploreResult | null>(null);
  const [hasRun, setHasRun] = useState(false);

  /* -- History pagination ------------------------------------------ */
  const [histPage, setHistPage] = useState(1);
  const histSize = 5;

  /* -- Create factor dialog ---------------------------------------- */
  const [showCreateFactor, setShowCreateFactor] = useState(false);

  /* -- Data catalog for availability ------------------------------- */
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);

  const effectiveInterval = dataType === "trade_tick" ? "tick" : interval;

  const dataAvail = useMemo(() => {
    const matches = catalog.filter(
      (c) => c.symbol === symbol && c.data_type === dataType && c.interval === effectiveInterval,
    );
    if (matches.length === 0) return null;
    const total = matches.reduce((s, c) => s + (c.record_count ?? 0), 0);
    const minDate = matches.map((c) => c.start_date).sort()[0];
    const maxDate = matches.map((c) => c.end_date).sort().reverse()[0];
    return { total, minDate, maxDate };
  }, [catalog, symbol, dataType, effectiveInterval]);

  /* -- Load initial data ------------------------------------------- */
  useEffect(() => {
    (async () => {
      try {
        const syms = await apiGet<SymbolOption[]>("/api/research/symbols");
        if (syms && syms.length > 0) setSymbols(syms);
      } catch {
        /* use fallback */
      }
      try {
        const groups = await apiGet<FactorGroup[]>("/api/research/factors");
        if (groups && groups.length > 0) setFactorGroups(groups);
      } catch {
        /* use fallback */
      }
      try {
        const cat = await apiGet<CatalogEntry[]>("/api/data/catalog");
        if (cat) setCatalog(cat);
      } catch {
        /* use fallback */
      }
    })();
    loadHistory();
  }, []);

  async function loadHistory() {
    setHistLoading(true);
    try {
      const jobs = await apiGet<HistoryJob[]>("/api/research/jobs");
      if (jobs) setHistJobs(jobs);
    } catch {
      /* empty */
    } finally {
      setHistLoading(false);
    }
  }

  /* -- Factor selection logic -------------------------------------- */
  const disabledFactors = useMemo(() => {
    if (selectedFactors.size < MAX_FACTORS) return new Set<string>();
    const allNames = factorGroups.flatMap((g) => g.factors.map((f) => f.name));
    return new Set(allNames.filter((n) => !selectedFactors.has(n)));
  }, [selectedFactors, factorGroups]);

  const toggleFactor = useCallback((name: string) => {
    setSelectedFactors((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else if (next.size < MAX_FACTORS) {
        next.add(name);
      }
      return next;
    });
  }, []);

  /* -- Factor param helpers ---------------------------------------- */
  const selectedFactorDefs = useMemo(() => {
    const all = factorGroups.flatMap((g) => g.factors);
    return all.filter((f) => selectedFactors.has(f.name));
  }, [factorGroups, selectedFactors]);

  const getParamValue = useCallback(
    (factorName: string, key: string, defaultVal: number): number =>
      factorParams[factorName]?.[key] ?? defaultVal,
    [factorParams],
  );

  const setParamValue = useCallback(
    (factorName: string, key: string, value: number) => {
      setFactorParams((prev) => ({
        ...prev,
        [factorName]: { ...prev[factorName], [key]: value },
      }));
    },
    [],
  );

  /* -- Date validation --------------------------------------------- */
  const dateError = useMemo(() => {
    if (!startDate || !endDate) return "请填写开始和结束日期";
    const s = new Date(startDate);
    const e = new Date(endDate);
    if (isNaN(s.getTime()) || isNaN(e.getTime())) return "日期格式无效";
    if (s >= e) return "结束日期必须晚于开始日期";
    return null;
  }, [startDate, endDate]);

  /* -- Actions ----------------------------------------------------- */
  const explore = useAction(
    () =>
      apiPost<ExploreResult>("/api/research/explore", {
        symbol,
        data_type: dataType,
        interval: effectiveInterval,
        start_date: startDate,
        end_date: endDate,
        factors: Array.from(selectedFactors),
        forward_period: forwardPeriod,
        quantiles,
        return_type: returnType,
        ...(dataType === "trade_tick" ? { tick_source: tickSource } : {}),
        factor_params: Object.fromEntries(
          selectedFactorDefs.map((f) => [
            f.name,
            Object.fromEntries(
              (f.params ?? []).map((p) => [p.key, getParamValue(f.name, p.key, p.default)]),
            ),
          ]),
        ),
      }),
    {
      onSuccess: (res) => {
        if (res) {
          setResult(res);
          setHasRun(true);
        }
      },
    },
  );

  const diagnose = useAction(
    () =>
      apiPost("/api/research/diagnose", {
        symbol,
        data_type: dataType,
        interval: effectiveInterval,
        start_date: startDate,
        end_date: endDate,
        factors: Array.from(selectedFactors),
        forward_period: forwardPeriod,
        quantiles,
        return_type: returnType,
        ...(dataType === "trade_tick" ? { tick_source: tickSource } : {}),
      }),
    {
      onSuccess: () => {
        loadHistory();
      },
    },
  );

  /* ================================================================ */
  /*  Render                                                           */
  /* ================================================================ */
  const actionsDisabled = selectedFactors.size === 0 || !!dateError;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* -- Page header -- */}
      <div className="flex justify-between items-center flex-shrink-0 px-6 py-3.5 border-b">
        <div>
          <div className="text-[1.05rem] font-bold leading-tight">Factor Research</div>
          <div className="font-mono text-[0.7rem] text-muted-foreground mt-0.5">
            // 探索因子 → 诊断验证 → 部署策略
          </div>
        </div>
      </div>

      {/* -- Explorer: left config + right results -- */}
      <div className="flex flex-1 overflow-hidden">
        {/* ===== LEFT: Config panel ===== */}
        <aside className="w-80 min-w-80 border-r overflow-y-auto bg-background p-4">
          <ResearchDatasetPanel
            symbols={symbols}
            symbol={symbol}
            onSymbolChange={setSymbol}
            dataType={dataType}
            onDataTypeChange={setDataType}
            interval={interval}
            onIntervalChange={setInterval}
            tickSource={tickSource}
            onTickSourceChange={setTickSource}
            startDate={startDate}
            onStartDateChange={setStartDate}
            endDate={endDate}
            onEndDateChange={setEndDate}
            dataAvail={dataAvail}
            dateError={dateError}
          />

          <ResearchFactorList
            factorGroups={factorGroups}
            selectedFactors={selectedFactors}
            openGroups={openGroups}
            disabledFactors={disabledFactors}
            onToggleGroup={toggleGroup}
            onToggleFactor={toggleFactor}
            onCreateFactor={() => setShowCreateFactor(true)}
          />

          <ResearchParams
            forwardPeriod={forwardPeriod}
            onForwardPeriodChange={setForwardPeriod}
            quantiles={quantiles}
            onQuantilesChange={setQuantiles}
            returnType={returnType}
            onReturnTypeChange={setReturnType}
            selectedFactorDefs={selectedFactorDefs}
            getParamValue={getParamValue}
            setParamValue={setParamValue}
          />

          {/* Actions */}
          <div className="flex flex-col gap-2 mt-5">
            <Button
              variant={explore.state === "error" ? "destructive" : "default"}
              onClick={explore.execute}
              disabled={explore.state === "loading" || actionsDisabled}
            >
              {explore.state === "loading" ? (
                "计算中..."
              ) : explore.state === "success" ? (
                "✓ 完成"
              ) : (
                <>
                  <Play className="w-3 h-3" />
                  运行探索
                </>
              )}
            </Button>
            {explore.state === "error" && explore.error && (
              <InlineError>{explore.error}</InlineError>
            )}

            <Button
              variant={
                diagnose.state === "error"
                  ? "destructive"
                  : diagnose.state === "success"
                  ? "default"
                  : "outline"
              }
              onClick={diagnose.execute}
              disabled={diagnose.state === "loading" || actionsDisabled}
            >
              {diagnose.state === "loading" ? (
                "提交中..."
              ) : diagnose.state === "success" ? (
                "✓ 已提交"
              ) : (
                <>
                  <Send className="w-3 h-3" />
                  提交深度诊断
                </>
              )}
            </Button>
            {diagnose.state === "error" && diagnose.error && (
              <InlineError>{diagnose.error}</InlineError>
            )}

            <Button
              variant="ghost"
              className="pointer-events-none opacity-40 justify-center"
              disabled
            >
              部署为策略
              <ArrowRight className="w-3 h-3" />
            </Button>
          </div>
        </aside>

        {/* ===== RIGHT: Results ===== */}
        <main className="flex-1 overflow-y-auto px-7 py-5 pb-16">
          {!hasRun && !result ? (
            <EmptyState
              variant="first-use"
              title="选择品种和因子，开始研究"
              description={
                "在左侧面板选择品种、勾选因子，然后点击\u201c运行探索\u201d查看结果。"
              }
              hint={
                <a
                  href="/data-catalog"
                  className="font-mono text-[0.72rem] text-primary hover:underline"
                >
                  没有数据？去数据目录拉取 →
                </a>
              }
            />
          ) : (
            <>
              <ResearchJobQueue
                histJobs={histJobs}
                histLoading={histLoading}
                histPage={histPage}
                histSize={histSize}
                onPageChange={setHistPage}
              />
              {result && <ResearchExploreResult result={result} />}
            </>
          )}
        </main>
      </div>

      {/* -- Create factor dialog -- */}
      <CreateFactorDialog
        open={showCreateFactor}
        onOpenChange={setShowCreateFactor}
        onCreated={setFactorGroups}
      />
    </div>
  );
}
