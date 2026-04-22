"use client";

import type { ReactNode } from "react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { HelpTip } from "@/components/qds";

interface ChartPanelProps {
  title: string;
  /** Right-aligned grey hint text (e.g. the chart sub-title). */
  sub?: string;
  /** Inline help bubble next to the title. */
  tip?: string;
  /** Arbitrary right-aligned badge (e.g. the p-value pill in shuffle test). */
  badge?: ReactNode;
  children: ReactNode;
  className?: string;
  /** Optional ``data-testid`` for E2E lookups. */
  testId?: string;
}

/**
 * Consistent chart-panel wrapper for the factor-report tabs.
 *
 * Wraps a shadcn ``<Card>`` with a two-line header (title + optional
 * help/sub/badge) — matches the Web UI Kit "card with sub" pattern and the
 * ExploreResult ``ChartHeader`` density so the explore → report navigation
 * feels continuous.
 */
export function ChartPanel({
  title,
  sub,
  tip,
  badge,
  children,
  className,
  testId,
}: ChartPanelProps) {
  return (
    <Card padding={false} className={className} data-testid={testId}>
      <CardHeader className="flex flex-row justify-between items-center px-3 py-2.5 border-b text-[0.72rem] font-semibold">
        <span className="flex items-center">
          {title}
          {tip && <HelpTip text={tip} />}
        </span>
        {badge ? (
          <span>{badge}</span>
        ) : sub ? (
          <span className="font-mono text-[0.58rem] font-normal text-muted-foreground">
            {sub}
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="p-3">{children}</CardContent>
    </Card>
  );
}
