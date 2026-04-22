"use client";

import { useMemo } from "react";
import { Hexagon } from "lucide-react";
import { SectionLabel } from "@/components/qds";
import { cn } from "@/lib/utils";
import { CATEGORY_ORDER } from "./types";
import type { FactorSpec } from "./types";

interface FactorListProps {
  factors: FactorSpec[];
  selected: string | null;
  onSelect: (name: string) => void;
  category: string;
  onCategoryChange: (category: string) => void;
  loading?: boolean;
}

/**
 * Factor list with a segmented category filter above a scrollable list.
 *
 * Design reference:
 *   - ``preview/component-tabs.html`` (segmented pill; uses ``bg-secondary``
 *     + ``text-foreground`` for the active tab, matches the underlying
 *     ``.tabs-seg`` pattern in Web UI Kit)
 *   - Web UI Kit `.row-list .r` pattern (3px accent stripe → accent-dim
 *     highlight when selected)
 *
 * Single-select by design — the explore panel visualises one factor at a
 * time (matches the /api/factor/explore request which takes ``factor_name``).
 */
export function FactorList({
  factors,
  selected,
  onSelect,
  category,
  onCategoryChange,
  loading,
}: FactorListProps) {
  /* Available categories — derived from data, intersected with canonical order. */
  const categories = useMemo(() => {
    const present = new Set(factors.map((f) => f.category));
    const canonical = CATEGORY_ORDER.filter((c) => c === "全部" || present.has(c));
    const extra = Array.from(present).filter((c) => !CATEGORY_ORDER.includes(c));
    return [...canonical, ...extra];
  }, [factors]);

  const filtered = useMemo(() => {
    if (category === "全部") return factors;
    return factors.filter((f) => f.category === category);
  }, [factors, category]);

  return (
    <section className="mb-5">
      <SectionLabel>因子库 · {factors.length} 个</SectionLabel>

      {/* Segmented pill filter — tabs-seg pattern */}
      <div
        className="inline-flex flex-wrap bg-input rounded-md p-[3px] gap-[2px] mb-2 max-w-full"
        role="tablist"
        aria-label="因子分类过滤"
      >
        {categories.map((c) => {
          const active = c === category;
          return (
            <button
              key={c}
              type="button"
              role="tab"
              aria-selected={active}
              data-testid={`factor-category-${c}`}
              onClick={() => onCategoryChange(c)}
              className={cn(
                "font-mono text-[0.66rem] px-[0.7rem] py-[0.35rem] rounded-[4px] transition-colors duration-150 ease-qds cursor-pointer",
                active
                  ? "bg-secondary text-foreground shadow-[0_1px_3px_rgba(0,0,0,0.1)]"
                  : "bg-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {c}
            </button>
          );
        })}
      </div>

      {/* Factor rows */}
      {loading ? (
        <div className="font-mono text-[0.7rem] text-muted-foreground px-2 py-3">
          加载因子列表中...
        </div>
      ) : filtered.length === 0 ? (
        <div className="font-mono text-[0.7rem] text-muted-foreground px-2 py-3">
          当前分类无因子
        </div>
      ) : (
        <div
          className="rounded-lg border bg-card overflow-hidden divide-y"
          data-testid="factor-list"
        >
          {filtered.map((f) => {
            const active = selected === f.name;
            return (
              <button
                key={f.name}
                type="button"
                role="option"
                aria-selected={active}
                data-testid={`factor-item-${f.name}`}
                onClick={() => onSelect(f.name)}
                className={cn(
                  "w-full grid grid-cols-[3px_1fr] items-stretch text-left transition-colors duration-150 ease-qds",
                  "cursor-pointer hover:bg-secondary",
                  active && "bg-qds-accent-dim",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "self-stretch",
                    active ? "bg-primary" : "bg-transparent",
                  )}
                />
                <span className="px-3 py-2 flex flex-col gap-1 min-w-0">
                  <span className="flex items-center gap-1.5">
                    <Hexagon
                      className={cn(
                        "w-3 h-3 shrink-0",
                        active ? "text-primary" : "text-muted-foreground",
                      )}
                    />
                    <span
                      className={cn(
                        "font-mono text-[0.76rem] font-semibold truncate",
                        active ? "text-primary" : "text-foreground",
                      )}
                    >
                      {f.name}
                    </span>
                    <span className="ml-auto font-mono text-[0.58rem] text-qds-t3 shrink-0">
                      lb {f.lookback}
                    </span>
                  </span>
                  <span
                    className="text-[0.65rem] text-muted-foreground line-clamp-2"
                    title={f.description}
                  >
                    {f.description || "—"}
                  </span>
                  <span className="flex items-center gap-1 font-mono text-[0.58rem] text-qds-t3">
                    <span className="bg-secondary text-foreground/70 rounded px-1.5 py-[1px]">
                      {f.category}
                    </span>
                    <span>v{f.version}</span>
                    {f.input_fields.length > 0 && (
                      <span className="truncate">
                        · {f.input_fields.join(", ")}
                      </span>
                    )}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
