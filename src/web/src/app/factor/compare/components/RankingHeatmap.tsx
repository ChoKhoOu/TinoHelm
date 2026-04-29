"use client";

import { HelpTip } from "@/components/qds";
import type { RankingHeatmap as RankingHeatmapData } from "../types";

interface RankingHeatmapProps {
  heatmap: RankingHeatmapData;
}

/**
 * Factor × Metric ranking heatmap.
 *
 * - One column per metric (``ic_mean``, ``ir`` from compare_multi).
 * - One row per factor.
 * - Cell text: raw metric value (4-decimal mono).
 * - Cell background: accent-orange ``--acc-d`` opacity scaled by rank — rank 1
 *   is the darkest, rank F the lightest.
 *
 * Charts Spec: pure tabular layout (no Recharts) so we use border + background
 * styling only.  Accent dim variants drive the heat scale; we do NOT introduce
 * any new color tokens.
 */
export function RankingHeatmap({ heatmap }: RankingHeatmapProps) {
  const { factors, metrics, values, rankings } = heatmap;
  const F = factors.length;
  const M = metrics.length;

  if (F === 0 || M === 0) {
    return (
      <div className="rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground font-mono">
        暂无可对比的因子数据
      </div>
    );
  }

  /** Map a 1-based rank → background opacity (1.0 = darkest, F = lightest). */
  const opacityForRank = (rank: number): number => {
    if (F <= 1) return 0.6;
    /* Scale 1..F → 0.55..0.10 linear. */
    const t = (rank - 1) / (F - 1);
    return 0.55 - t * 0.45;
  };

  return (
    <div className="rounded-lg border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b flex items-center gap-2">
        <span className="font-mono text-[0.7rem] uppercase tracking-widest text-primary">
          Ranking Heatmap
        </span>
        <HelpTip text="每个 metric 列内 1-based 排名（高值 → rank 1）。颜色越深 = 排名越靠前。" />
        <span className="font-mono text-[0.62rem] text-muted-foreground ml-auto">
          {F} 因子 × {M} 指标
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b">
              <th className="text-left font-mono text-[0.65rem] uppercase tracking-wider text-muted-foreground px-4 py-2 sticky left-0 bg-card">
                Factor
              </th>
              {metrics.map((m) => (
                <th
                  key={m}
                  className="text-right font-mono text-[0.65rem] uppercase tracking-wider text-muted-foreground px-3 py-2 min-w-[120px]"
                >
                  {m}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {factors.map((factor, i) => (
              <tr
                key={factor}
                className="border-b last:border-b-0 hover:bg-secondary transition-colors"
              >
                <td className="font-mono text-[0.78rem] font-medium px-4 py-2 sticky left-0 bg-card">
                  {factor}
                </td>
                {metrics.map((m, j) => {
                  const value = values[i]?.[j] ?? null;
                  const rank = rankings[i]?.[j] ?? F + 1;
                  const op = opacityForRank(rank);
                  return (
                    <td
                      key={m}
                      className="text-right font-mono text-[0.75rem] px-3 py-2 relative"
                    >
                      <div
                        className="absolute inset-0.5 rounded-sm bg-primary"
                        style={{ opacity: op }}
                      />
                      <div className="relative flex items-center justify-end gap-2">
                        <span className="text-[0.6rem] text-muted-foreground">
                          #{rank}
                        </span>
                        <span className="text-foreground">
                          {value == null ? "—" : value.toFixed(4)}
                        </span>
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
