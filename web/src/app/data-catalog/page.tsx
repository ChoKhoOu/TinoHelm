"use client";

import { useState, useEffect } from "react";
import { Upload } from "lucide-react";
import { apiGet } from "@/lib/api";
import { useI18n } from "@/i18n";

interface CatalogEntry {
  symbol: string;
  data_type: string;
  interval: string;
  start_date: string;
  end_date: string;
  file_path: string;
  size_bytes: number;
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

function getTypeColor(dataType: string): string {
  if (dataType === "TRADE_TICK") {
    return "bg-[var(--accent-green-20)] text-[var(--accent-green)]";
  }
  if (dataType.startsWith("BAR_")) {
    return "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]";
  }
  if (dataType === "QUOTE_TICK") {
    return "bg-[var(--accent-orange-20)] text-[var(--accent-orange)]";
  }
  return "bg-[var(--bg-subtle)] text-[var(--text-secondary)]";
}

export default function DataCatalogPage() {
  const { t } = useI18n();
  const [datasets, setDatasets] = useState<CatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadCatalog() {
      try {
        const data = await apiGet<CatalogEntry[]>("/api/data/catalog");
        if (cancelled) return;
        if (data) {
          setDatasets(data);
        }
      } catch {
        if (!cancelled) setError("common.loadFailed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadCatalog();
    return () => { cancelled = true; };
  }, []);

  const totalSize = datasets.reduce((s, d) => s + d.size_bytes, 0);
  const allDates = datasets.flatMap((d) => [d.start_date, d.end_date]).filter(Boolean).sort();
  const minDate = allDates.length > 0 ? allDates[0] : "";
  const maxDate = allDates.length > 0 ? allDates[allDates.length - 1] : "";

  const stats = [
    { label: t("dataCatalog.totalDatasets"), value: String(datasets.length) },
    { label: t("dataCatalog.totalSize"), value: formatBytes(totalSize) },
    { label: t("dataCatalog.dateRange"), value: datasets.length > 0 ? `${minDate} \u2192 ${maxDate}` : "\u2014", accent: datasets.length > 0 },
    { label: t("dataCatalog.storage"), value: "LOCAL" },
  ];

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <span className="font-mono text-[12px] text-[var(--accent-red)]">{t("common.loadFailed")}</span>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-6 p-8">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("dataCatalog.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("dataCatalog.loading")}
          </span>
        </div>
        <div className="flex items-center justify-center min-h-[300px]">
          <div className="flex flex-col items-center gap-3">
            <div className="w-6 h-6 border-2 border-[var(--accent-green)] border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-[var(--text-muted)]">{t("dataCatalog.loading")}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6 p-8">
      {/* Top bar */}
      <div className="flex items-end justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("dataCatalog.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("dataCatalog.subtitle")}
          </span>
        </div>
        <button aria-label="Import data" className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-5 py-[10px] text-[11px] font-bold tracking-wide hover:opacity-90 transition-all duration-150">
          <Upload className="w-3 h-3" />
          {t("dataCatalog.importData")}
        </button>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-4 gap-4">
        {stats.map((s) => (
          <div
            key={s.label}
            className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-4"
          >
            <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
              {s.label}
            </span>
            <div
              className={`font-heading text-2xl font-bold mt-2 ${
                s.accent
                  ? "text-[var(--accent-green)]"
                  : "text-[var(--text-primary)]"
              }`}
            >
              {s.value}
            </div>
          </div>
        ))}
      </div>

      {/* Data table */}
      {datasets.length === 0 ? (
        <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] flex items-center justify-center min-h-[200px]">
          <span className="text-xs text-[var(--text-muted)]">
            {t("dataCatalog.noDatasets")}
          </span>
        </div>
      ) : (
        <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)]">
          {/* Header */}
          <div className="flex items-center px-5 py-3 border-b border-[var(--border-gray)]">
            <span className="w-[200px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
              {t("common.instrument")}
            </span>
            <span className="w-[120px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
              {t("common.type")}
            </span>
            <span className="w-[200px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
              {t("common.range")}
            </span>
            <span className="w-[100px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
              {t("common.interval")}
            </span>
            <span className="w-[80px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
              {t("common.size")}
            </span>
          </div>
          {/* Rows */}
          {datasets.map((ds, i) => (
            <div
              key={`${ds.symbol}-${ds.data_type}-${i}`}
              className={`flex items-center px-5 py-[11px] text-[11px] font-medium ${
                i < datasets.length - 1
                  ? "border-b border-[var(--border-gray)]"
                  : ""
              }`}
            >
              <span className="w-[200px] text-[var(--text-primary)]">
                {ds.symbol}
              </span>
              <span className="w-[120px]">
                <span
                  className={`inline-flex rounded-full px-[10px] py-1 text-[9px] font-bold ${getTypeColor(ds.data_type)}`}
                >
                  {ds.data_type}
                </span>
              </span>
              <span className="w-[200px] text-[var(--text-secondary)]">
                {ds.start_date} &rarr; {ds.end_date}
              </span>
              <span className="w-[100px] text-[var(--text-secondary)]">
                {ds.interval}
              </span>
              <span className="w-[80px] text-[var(--text-secondary)]">
                {formatBytes(ds.size_bytes)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
