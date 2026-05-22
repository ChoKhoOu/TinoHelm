"use client";

import { cn } from "@/lib/utils";
import { StatCard } from "@/components/qds";
import { ChartPanel } from "./ChartPanel";
import { CrossSymbolICChart } from "./CrossSymbolICChart";
import { ShuffleHistogramChart } from "./ShuffleHistogramChart";
import { SubsampleICChart } from "./SubsampleICChart";
import type { EvalResultPayload } from "./types";

interface RobustnessTabProps {
  result: EvalResultPayload;
}

/**
 * Tab 3 — Robustness.
 *
 * Three stress-tests ensure the IC signal isn't luck:
 *   1. **Shuffle test** — compares real IC to a null distribution from
 *      N random permutations.  Significant when ``p_value < 0.05``.
 *   2. **Subsample IC** — month/quarter-segmented IC; a stable factor
 *      should stay positive most periods.
 *   3. **Cross-symbol IC** — IC for each instrument in the universe;
 *      high dispersion suggests single-symbol overfit.
 *
 * Robustness data is present **only** when the backend runs
 * ``evaluate_full()`` (i.e. the ``/run`` deep path).  The explore-only
 * fast path leaves ``robustness`` as ``{}`` — we render a "需深度诊断"
 * hint in that case.
 */
export function RobustnessTab({ result }: RobustnessTabProps) {
  const robustness = result.robustness ?? {};
  const shuffle = robustness.shuffle;
  const subsample = robustness.subsample ?? [];
  const crossSymbol = robustness.cross_symbol ?? [];

  const hasAny =
    shuffle != null ||
    subsample.length > 0 ||
    crossSymbol.length > 0;

  if (!hasAny) {
    return (
      <div
        className="animate-qds-fade-up"
        data-testid="factor-report-tab-robust"
      >
        <div className="rounded-lg border bg-card p-8 text-center">
          <div className="text-[0.8rem] font-semibold mb-2">
            暂无稳健性诊断
          </div>
          <div className="font-mono text-[0.68rem] text-muted-foreground leading-relaxed max-w-md mx-auto">
            稳健性分析（shuffle test / 分段 IC / 跨品种 IC）需通过
            <code className="mx-1 text-primary">POST /api/factor/run</code>
            以 ``full=true`` 运行，当前 run 仅含 explore 快照。
          </div>
        </div>
      </div>
    );
  }

  /* Count positive segments for the subsample panel badge. */
  const positiveSegments = subsample.filter((s) => s.ic > 0).length;
  const positivePct =
    subsample.length > 0
      ? Math.round((positiveSegments / subsample.length) * 100)
      : 0;

  /* Count positive symbols for cross-symbol dispersion. */
  const positiveSymbols = crossSymbol.filter((c) => c.ic > 0).length;
  const crossPct =
    crossSymbol.length > 0
      ? Math.round((positiveSymbols / crossSymbol.length) * 100)
      : 0;

  return (
    <div
      className="animate-qds-fade-up"
      data-testid="factor-report-tab-robust"
    >
      {/* KPI row — summary of all 3 tests */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-5">
        <StatCard
          label="Shuffle p-value"
          value={
            shuffle ? shuffle.p_value.toFixed(3) : "—"
          }
          trend={
            shuffle
              ? shuffle.significant
                ? "up"
                : "down"
              : undefined
          }
          sub={
            shuffle
              ? shuffle.significant
                ? "显著 (p < 0.05)"
                : "不显著"
              : "—"
          }
          help="与随机置乱的 IC 分布比较；p < 0.05 即显著"
        />
        <StatCard
          label="Real IC"
          value={shuffle ? shuffle.real_ic.toFixed(4) : "—"}
          help="观察到的真实 IC，用于与 shuffle 分布对照"
        />
        <StatCard
          label="正段占比"
          value={`${positivePct}%`}
          sub={`${positiveSegments}/${subsample.length} 段`}
          trend={
            positivePct >= 70
              ? "up"
              : positivePct < 50
                ? "down"
                : undefined
          }
          help="分段 IC 中正值段数占比，>=70% 算稳健"
        />
        <StatCard
          label="跨品种正号"
          value={`${crossPct}%`}
          sub={`${positiveSymbols}/${crossSymbol.length} 品种`}
          trend={
            crossPct >= 70
              ? "up"
              : crossPct < 50
                ? "down"
                : undefined
          }
          help="IC 为正的品种占比"
        />
      </div>

      {/* Row 1: shuffle + subsample */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <ChartPanel
          title="Shuffle Test"
          tip="对因子值随机置乱 N 次构造 null 分布；real IC 落在尾部即说明信号显著。"
          badge={
            shuffle ? (
              <span
                className={cn(
                  "font-mono text-[0.62rem] px-2 py-0.5 rounded-full",
                  shuffle.significant
                    ? "bg-qds-success-dim text-qds-success"
                    : "bg-qds-danger-dim text-destructive",
                )}
              >
                p = {shuffle.p_value.toFixed(3)}
              </span>
            ) : undefined
          }
          testId="factor-report-chart-shuffle"
        >
          {shuffle ? (
            <ShuffleHistogramChart
              distribution={shuffle.shuffle_distribution}
              realIc={shuffle.real_ic}
            />
          ) : (
            <div className="h-[220px] flex items-center justify-center text-[0.72rem] text-muted-foreground font-mono">
              未执行 shuffle 测试
            </div>
          )}
        </ChartPanel>

        <ChartPanel
          title="分段 IC"
          tip="按月/季度分段计算 IC，观察信号在时间上的稳定性。"
          sub={`正段 ${positiveSegments}/${subsample.length}`}
          testId="factor-report-chart-subsample"
        >
          <SubsampleICChart data={subsample} />
        </ChartPanel>
      </div>

      {/* Row 2: cross-symbol (full-width horizontal bar) */}
      <ChartPanel
        title="跨品种 IC"
        tip="同一因子在不同品种上的 IC 分布；高离散度说明可能是单品种过拟合。"
        sub={`${crossSymbol.length} symbols`}
        testId="factor-report-chart-cross-symbol"
      >
        <CrossSymbolICChart
          data={crossSymbol}
          height={Math.max(220, crossSymbol.length * 28 + 40)}
        />
      </ChartPanel>
    </div>
  );
}
