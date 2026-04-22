import { FactorExploreClient } from "./FactorExploreClient";

export const metadata = {
  title: "Factor Explore · TinoHelm",
};

/**
 * /factor — declarative factor framework explore page.
 *
 * Route is a thin server-component wrapper around the client shell so static
 * export (``output: "export"`` in ``next.config.ts``) keeps working without
 * server-side data fetching.  All API calls happen inside the client
 * component via the ``useFactorList`` / ``useExplore`` hooks.
 */
export default function FactorPage() {
  return <FactorExploreClient />;
}
