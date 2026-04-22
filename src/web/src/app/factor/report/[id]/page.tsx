import { ReportClient } from "./ReportClient";

export const metadata = {
  title: "Factor Report · TinoHelm",
};

/**
 * /factor/report/[id] — declarative factor framework diagnostic report.
 *
 * Mirrors the /research/report/[id] pattern: ``generateStaticParams`` returns
 * a single placeholder so Next.js static export writes one HTML shell; the
 * real ``run_id`` is resolved at runtime on the client via ``useParams``.
 *
 * All data fetching happens inside ``ReportClient`` through
 * ``useReport(run_id)`` which hits ``GET /api/factor/report/{run_id}``.
 */
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function FactorReportPage() {
  return <ReportClient />;
}
