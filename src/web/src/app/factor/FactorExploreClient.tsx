"use client";

import { useMemo, useState } from "react";
import { Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/EmptyState";
import { InlineError } from "@/components/qds";
import { useFactorList } from "./hooks/useFactorList";
import { useExplore } from "./hooks/useExplore";
import { DatasetPanel } from "./components/DatasetPanel";
import { FactorList } from "./components/FactorList";
import { ParamsPanel } from "./components/ParamsPanel";
import { ExploreResult } from "./components/ExploreResult";
import type {
  ExploreRequest,
  FactorParams,
  FactorSpec,
} from "./components/types";

/**
 * Factor explore page shell — left-side configuration + right-side result
 * panel, mirroring the Web UI Kit app frame layout (sidebar lives in the
 * root layout, so we only render the main content here).
 *
 * Data flow:
 *   1. ``useFactorList``  loads factor metadata + universes + symbols on mount
 *   2. User selects one factor, adjusts eval config + factor params
 *   3. ``useExplore``     POSTs /api/factor/explore and stashes ``ExploreResult``
 *   4. ``<ExploreResult>`` renders the 4 charts + summary KPIs
 *
 * Error strategy: API errors surface as ``<InlineError />`` next to the
 * action button (Layer-2 QDS contract — no toast).
 */
export function FactorExploreClient() {
  /* ---------------------------------------------------------------- */
  /*  Metadata + selection state                                       */
  /* ---------------------------------------------------------------- */
  const { factors, universes, symbols, loading, error, reload } =
    useFactorList();

  const [category, setCategory] = useState("全部");
  /* null = no explicit user selection → falls back to first factor */
  const [selectedFactorName, setSelectedFactorName] = useState<string | null>(
    null,
  );

  /* Derive the effective selection without a setState-in-effect. */
  const effectiveFactorName = selectedFactorName ?? factors[0]?.name ?? null;

  const selectedFactor: FactorSpec | null = useMemo(
    () => factors.find((f) => f.name === effectiveFactorName) ?? null,
    [factors, effectiveFactorName],
  );

  /* ---------------------------------------------------------------- */
  /*  Dataset state                                                    */
  /* ---------------------------------------------------------------- */
  /* null = no explicit user selection → falls back to first universe */
  const [universeOverride, setUniverseOverride] = useState<string | null>(null);
  const universe = universeOverride ?? universes[0] ?? "";
  const setUniverse = (v: string) => setUniverseOverride(v);

  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([]);
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2025-04-01");

  const dateError = useMemo(() => {
    if (!startDate || !endDate) return "请填写开始和结束日期";
    const s = new Date(startDate);
    const e = new Date(endDate);
    if (!Number.isFinite(s.getTime()) || !Number.isFinite(e.getTime())) {
      return "日期格式无效";
    }
    if (s >= e) return "结束日期必须晚于开始日期";
    return null;
  }, [startDate, endDate]);

  /* ---------------------------------------------------------------- */
  /*  Eval config + factor params                                      */
  /* ---------------------------------------------------------------- */
  const [forwardPeriod, setForwardPeriod] = useState(5);
  const [quantiles, setQuantiles] = useState(5);
  const [icFreq, setIcFreq] = useState("D");
  const [costBps, setCostBps] = useState(4.0);
  const [logRet, setLogRet] = useState(false);

  /* Per-factor param overrides — keyed by factor name so switching factors
     doesn't lose previously-tuned overrides for the other. */
  const [paramsByFactor, setParamsByFactor] = useState<
    Record<string, FactorParams>
  >({});

  const factorParams: FactorParams = useMemo(() => {
    if (!selectedFactor) return {};
    return paramsByFactor[selectedFactor.name] ?? selectedFactor.params_schema;
  }, [selectedFactor, paramsByFactor]);

  const setFactorParams = (params: FactorParams) => {
    if (!selectedFactor) return;
    setParamsByFactor((prev) => ({ ...prev, [selectedFactor.name]: params }));
  };

  /* ---------------------------------------------------------------- */
  /*  Explore action                                                   */
  /* ---------------------------------------------------------------- */
  const explore = useExplore();

  const runExplore = () => {
    if (!selectedFactor || dateError) return;

    const payload: ExploreRequest = {
      factor_name: selectedFactor.name,
      config: {
        universe:
          selectedSymbols.length > 0 ? selectedSymbols : symbols.length > 0 ? symbols : [universe],
        start: startDate,
        end: endDate,
        forward_period: forwardPeriod,
        quantiles,
        ic_freq: icFreq,
        cost_bps: costBps,
        log_ret: logRet,
      },
      params: factorParams,
    };

    return explore.execute(payload);
  };

  const actionsDisabled =
    !selectedFactor ||
    !!dateError ||
    (!universe && universes.length > 0) ||
    loading;

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */
  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Page header */}
      <div className="flex justify-between items-center flex-shrink-0 px-6 py-3.5 border-b">
        <div>
          <div className="text-[1.05rem] font-bold leading-tight">
            Factor Explore
          </div>
          <div className="font-mono text-[0.7rem] text-muted-foreground mt-0.5">
            声明式因子框架 · 探索单因子预测力与换手
          </div>
        </div>
        {error && (
          <button
            type="button"
            onClick={reload}
            className="font-mono text-[0.68rem] text-destructive hover:underline cursor-pointer"
          >
            加载失败 · 重试
          </button>
        )}
      </div>

      {/* Two-pane body */}
      <div className="flex flex-1 overflow-hidden">
        {/* LEFT · Configuration */}
        <aside className="w-80 min-w-80 border-r overflow-y-auto bg-background p-4">
          <DatasetPanel
            universes={universes}
            universe={universe}
            onUniverseChange={setUniverse}
            symbols={symbols}
            selectedSymbols={selectedSymbols}
            onSelectedSymbolsChange={setSelectedSymbols}
            startDate={startDate}
            onStartDateChange={setStartDate}
            endDate={endDate}
            onEndDateChange={setEndDate}
            dateError={dateError}
          />

          <FactorList
            factors={factors}
            selected={effectiveFactorName}
            onSelect={setSelectedFactorName}
            category={category}
            onCategoryChange={setCategory}
            loading={loading}
          />

          <ParamsPanel
            factor={selectedFactor}
            forwardPeriod={forwardPeriod}
            onForwardPeriodChange={setForwardPeriod}
            quantiles={quantiles}
            onQuantilesChange={setQuantiles}
            icFreq={icFreq}
            onIcFreqChange={setIcFreq}
            costBps={costBps}
            onCostBpsChange={setCostBps}
            logRet={logRet}
            onLogRetChange={setLogRet}
            factorParams={factorParams}
            onFactorParamsChange={setFactorParams}
          />

          {/* Actions */}
          <div className="flex flex-col gap-2 mt-5">
            <Button
              variant={explore.state === "error" ? "destructive" : "default"}
              onClick={runExplore}
              disabled={explore.state === "loading" || actionsDisabled}
              data-testid="run-explore"
            >
              {explore.state === "loading" ? (
                "计算中..."
              ) : explore.state === "success" ? (
                "✓ 已完成"
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
            {error && (
              <InlineError>{`元数据加载失败: ${error}`}</InlineError>
            )}
          </div>
        </aside>

        {/* RIGHT · Result */}
        <main
          className="flex-1 overflow-y-auto px-7 py-5 pb-16"
          data-testid="factor-result-panel"
        >
          {!explore.hasRun ? (
            <EmptyState
              variant="first-use"
              title={
                selectedFactor
                  ? `准备探索 · ${selectedFactor.name}`
                  : "选择因子开始探索"
              }
              description={
                "在左侧面板选择 universe、因子和参数，点击“运行探索”查看 IC / 分位 / 分布 / Turnover。"
              }
              hint={
                universes.length === 0 ? (
                  <span>
                    尚未配置 universe · 请在{" "}
                    <code className="text-primary">
                      ~/.tino/research/universes/
                    </code>{" "}
                    放置 CSV
                  </span>
                ) : undefined
              }
            />
          ) : (
            explore.result && (
              <ExploreResult result={explore.result} costBps={costBps} />
            )
          )}
        </main>
      </div>
    </div>
  );
}
