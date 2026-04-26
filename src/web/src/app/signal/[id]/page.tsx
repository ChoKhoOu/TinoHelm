import { SignalDetailClient } from "./SignalDetailClient";

export const metadata = {
  title: "Signal Detail · TinoHelm",
};

/**
 * /signal/[id] — single SignalRun detail page (static export shell).
 *
 * Next.js ``output: "export"`` requires every dynamic route to provide
 * ``generateStaticParams``.  We emit a single placeholder ``_`` so a HTML
 * shell ships, and the real ``run_id`` is read at runtime via
 * ``useParams()`` inside ``SignalDetailClient``.
 *
 * Pairs with backend ``GET /api/signal/report/{run_id}``.
 */
export function generateStaticParams() {
  return [{ id: "_" }];
}

export default function SignalDetailPage() {
  return <SignalDetailClient />;
}
