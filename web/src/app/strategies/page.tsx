"use client";

import { useState, useEffect } from "react";
import { apiGet } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { useI18n } from "@/i18n";

interface StrategyInfo {
  name: string;
  file_path: string;
  config_schema?: Record<string, unknown>;
  hooks?: string[];
}

export default function StrategiesPage() {
  const { t } = useI18n();
  const [strategies, setStrategies] = useState<StrategyInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadStrategies() {
      try {
        const data = await apiGet<StrategyInfo[]>("/api/strategies");
        if (cancelled) return;
        if (data) {
          setStrategies(data);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load strategies");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadStrategies();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div className="flex flex-col gap-5 p-6">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("strategies.title")}
          </h1>
          <span className="text-xs text-[var(--text-muted)]">
            // LOADING...
          </span>
        </div>
        <div className="flex items-center justify-center min-h-[300px]">
          <div className="flex flex-col items-center gap-3">
            <div className="w-6 h-6 border-2 border-[var(--accent-green)] border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-[var(--text-muted)]">{t("strategies.loading")}</span>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col gap-5 p-6">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("strategies.title")}
          </h1>
          <span className="text-xs text-[var(--text-muted)]">
            {t("strategies.subtitle")}
          </span>
        </div>
        <div className="flex items-center justify-center min-h-[300px]">
          <div className="flex flex-col items-center gap-3">
            <span className="text-xs text-[var(--accent-red)]">{t("strategies.loadFailed")}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 p-6">
      {/* Top bar */}
      <div className="flex items-center justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("strategies.title")}
          </h1>
          <span className="text-xs text-[var(--text-muted)]">
            // {strategies.length} {t("strategies.discovered")}
          </span>
        </div>
      </div>

      {/* Strategy cards grid */}
      {strategies.length === 0 ? (
        <div className="flex items-center justify-center min-h-[300px]">
          <div className="flex flex-col items-center gap-3">
            <span className="text-xs text-[var(--text-muted)]">
              {t("strategies.noStrategies")}
            </span>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {strategies.map((strategy) => {
            const configCount = strategy.config_schema
              ? Object.keys(strategy.config_schema).length
              : 0;

            return (
              <Card key={strategy.name}>
                <div className="flex flex-col gap-3">
                  <div className="flex items-center justify-between">
                    <h2 className="text-sm font-bold tracking-tight text-[var(--text-primary)]">
                      {strategy.name}
                    </h2>
                    {configCount > 0 && (
                      <Badge variant="info">
                        {configCount} PARAM{configCount !== 1 ? "S" : ""}
                      </Badge>
                    )}
                  </div>

                  <span className="text-[10px] font-medium text-[var(--text-muted)]">
                    // {strategy.file_path}
                  </span>

                  {strategy.hooks && strategy.hooks.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-1">
                      {strategy.hooks.map((hook) => (
                        <span
                          key={hook}
                          className="inline-flex rounded-full px-[10px] py-1 text-[9px] font-bold bg-[var(--accent-green-20)] text-[var(--accent-green)]"
                        >
                          {hook}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
