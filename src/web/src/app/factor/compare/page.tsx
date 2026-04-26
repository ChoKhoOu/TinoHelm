import { CompareClient } from "./CompareClient";

export const metadata = {
  title: "Factor Compare · TinoHelm",
};

/**
 * /factor/compare — multi-factor comparison page.
 *
 * Server component shell.  All data flow lives in ``CompareClient`` because
 * the page is exported as a static SPA (``output: "export"`` in
 * ``next.config.ts``) and cannot do server-side fetching.
 *
 * Pairs with the s11 backend endpoint
 * ``POST /api/factor/compare/multi`` which returns ``ranking_heatmap``,
 * ``rolling_ic_small_multiples``, ``dendrogram``, ``ic_time_series_corr``
 * and an ``agent_summary`` string.
 */
export default function FactorComparePage() {
  return <CompareClient />;
}
