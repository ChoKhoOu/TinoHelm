"use client";

import { Server } from "lucide-react";
import { useState, useEffect } from "react";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { apiGet } from "@/lib/api";
import { useI18n } from "@/i18n";

/* ── Types ──────────────────────────────────────────────────── */

interface PositionItem {
  instrument_id: string;
  side: string;
  quantity: string;
  avg_price: string;
  unrealized_pnl: string;
  node_type: string;
}

/* ── Helpers ────────────────────────────────────────────────── */

function safeFloat(val: string): number {
  const n = parseFloat(val);
  return Number.isNaN(n) ? 0 : n;
}

/* ── Page ───────────────────────────────────────────────────── */

export default function PortfolioPage() {
  const { t } = useI18n();
  const [positions, setPositions] = useState<PositionItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadPositions() {
      try {
        const data = await apiGet<PositionItem[]>("/api/portfolio/allocation");
        if (cancelled) return;
        if (data) setPositions(data);
      } catch {
        if (!cancelled) setError("common.loadFailed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadPositions();
    return () => { cancelled = true; };
  }, []);

  /* Compute allocations from positions */
  const totalValue = positions.reduce(
    (s, p) => s + Math.abs(safeFloat(p.quantity) * safeFloat(p.avg_price)),
    0
  );

  const allocationColors: Record<string, string> = {
    CRYPTO: "var(--accent-green)",
    EQUITIES: "#4488FF",
    CASH: "var(--text-primary)",
  };

  // Group by a simple category based on instrument suffix
  const grouped: Record<string, number> = {};
  for (const p of positions) {
    const val = Math.abs(safeFloat(p.quantity) * safeFloat(p.avg_price));
    const cat = p.node_type.toUpperCase();
    grouped[cat] = (grouped[cat] || 0) + val;
  }

  const allocations = Object.entries(grouped).map(([label, amount]) => ({
    label,
    pct: totalValue > 0 ? (amount / totalValue) * 100 : 0,
    color: allocationColors[label] || "var(--accent-green)",
    amount: `$${amount.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`,
  }));

  /* Group venues from positions by node_type */
  const venueMap: Record<string, { positions: number; exposure: number; unrealized: number }> = {};
  for (const p of positions) {
    const venue = p.node_type.toUpperCase();
    if (!venueMap[venue]) venueMap[venue] = { positions: 0, exposure: 0, unrealized: 0 };
    venueMap[venue].positions += 1;
    venueMap[venue].exposure += Math.abs(safeFloat(p.quantity) * safeFloat(p.avg_price));
    venueMap[venue].unrealized += safeFloat(p.unrealized_pnl || "0");
  }

  const venues = Object.entries(venueMap).map(([venue, v]) => ({
    venue,
    balance: `$${v.exposure.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`,
    positions: v.positions,
    exposure: `$${v.exposure.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`,
    unrealized: `${v.unrealized >= 0 ? "+" : ""}$${v.unrealized.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
    unrealizedType: v.unrealized >= 0 ? ("positive" as const) : ("negative" as const),
    status: "ACTIVE" as const,
  }));

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <span className="font-mono text-[12px] text-[var(--accent-red)]">{t("common.loadFailed")}</span>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col h-full p-6 gap-5">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("portfolio.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("portfolio.subtitle")}
          </span>
        </div>
        <div className="flex items-center justify-center flex-1 text-[11px] text-[var(--text-muted)]">
          {t("portfolio.loading")}
        </div>
      </div>
    );
  }

  if (positions.length === 0) {
    return (
      <div className="flex flex-col h-full p-6 gap-5">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("portfolio.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("portfolio.subtitle")}
          </span>
        </div>
        <div className="flex items-center justify-center flex-1 text-[11px] text-[var(--text-muted)]">
          {t("portfolio.noPositions")}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full p-6 gap-5">
      {/* Title */}
      <div className="flex flex-col gap-1">
        <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
          PORTFOLIO
        </h1>
        <span className="text-[11px] font-medium text-[var(--text-muted)]">
          // ASSET ALLOCATION & EXPOSURE BREAKDOWN
        </span>
      </div>

      {/* Allocation Cards */}
      <div className="grid grid-cols-3 gap-4">
        {allocations.map((a) => (
          <Card key={a.label} className="p-5">
            <div className="flex flex-col gap-3">
              <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
                {a.label}
              </span>
              <span
                className="font-heading text-[28px] font-bold tracking-tight"
                style={{ color: a.color }}
              >
                {a.pct.toFixed(1)}%
              </span>
              {/* Progress bar */}
              <div className="w-full h-1 rounded-full bg-[var(--bg-elevated)]">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${a.pct}%`,
                    backgroundColor: a.color,
                  }}
                />
              </div>
              <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                {a.amount}
              </span>
            </div>
          </Card>
        ))}
      </div>

      {/* Venue Exposure Table */}
      <div className="flex flex-col rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden flex-1 min-h-0">
        {/* Header */}
        <div className="flex items-center gap-2 px-5 py-[14px]">
          <Server className="w-4 h-4 text-[var(--text-muted)]" />
          <span className="text-[11px] font-semibold tracking-[0.5px] text-[var(--text-secondary)]">
            {t("portfolio.venueExposure")}
          </span>
        </div>
        <div className="h-px bg-[var(--border-gray)]" />

        {/* Table */}
        <div className="overflow-auto flex-1">
          <table className="w-full">
            <thead>
              <tr className="border-b border-[var(--border-gray)]">
                {[
                  "VENUE",
                  "BALANCE",
                  "POSITIONS",
                  "EXPOSURE",
                  "UNREALIZED",
                  "STATUS",
                ].map((col) => (
                  <th
                    key={col}
                    className="px-5 py-3 text-left text-[9px] font-semibold tracking-[0.5px] text-[var(--text-muted)]"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {venues.map((v) => (
                <tr
                  key={v.venue}
                  className="border-b border-[var(--border-gray)] last:border-b-0 hover:bg-[var(--bg-elevated)] transition-colors duration-100"
                >
                  <td className="px-5 py-3 text-[11px] font-semibold text-[var(--text-primary)]">
                    {v.venue}
                  </td>
                  <td className="px-5 py-3 text-[11px] font-medium text-[var(--text-secondary)]">
                    {v.balance}
                  </td>
                  <td className="px-5 py-3 text-[11px] font-medium text-[var(--text-secondary)]">
                    {v.positions}
                  </td>
                  <td className="px-5 py-3 text-[11px] font-medium text-[var(--text-secondary)]">
                    {v.exposure}
                  </td>
                  <td
                    className={`px-5 py-3 text-[11px] font-semibold ${
                      v.unrealizedType === "positive"
                        ? "text-[var(--accent-green)]"
                        : "text-[var(--accent-red)]"
                    }`}
                  >
                    {v.unrealized}
                  </td>
                  <td className="px-5 py-3">
                    <Badge variant="success" dot>
                      {v.status}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
