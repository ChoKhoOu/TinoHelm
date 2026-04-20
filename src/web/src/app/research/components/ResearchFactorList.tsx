"use client";

import { ChevronRight, Plus } from "lucide-react";
import { SectionLabel } from "@/components/qds";
import { cn } from "@/lib/utils";
import { MAX_FACTORS } from "./types";
import type { FactorGroup } from "./types";

interface ResearchFactorListProps {
  factorGroups: FactorGroup[];
  selectedFactors: Set<string>;
  openGroups: Set<string>;
  disabledFactors: Set<string>;
  onToggleGroup: (group: string) => void;
  onToggleFactor: (name: string) => void;
  onCreateFactor: () => void;
}

export function ResearchFactorList({
  factorGroups,
  selectedFactors,
  openGroups,
  disabledFactors,
  onToggleGroup,
  onToggleFactor,
  onCreateFactor,
}: ResearchFactorListProps) {
  return (
    <section className="mb-5">
      <SectionLabel>因子选择</SectionLabel>

      {/* Limit indicator */}
      <div className="font-mono text-[0.6rem] text-qds-t3 mb-2 px-1">
        <span
          className={cn(
            "text-foreground",
            selectedFactors.size >= MAX_FACTORS && "text-qds-warning",
          )}
        >
          {selectedFactors.size}
        </span>{" "}
        / {MAX_FACTORS} 已选
      </div>

      {factorGroups.map((g) => {
        const isOpen = openGroups.has(g.group);
        const selCount = g.factors.filter((f) => selectedFactors.has(f.name)).length;
        return (
          <div
            key={g.group}
            className="border rounded-md mb-1.5 overflow-hidden transition-colors hover:border-qds-border-hover"
          >
            <button
              type="button"
              onClick={() => onToggleGroup(g.group)}
              className="w-full flex items-center justify-between px-2.5 py-2 text-[0.72rem] font-medium bg-card hover:bg-secondary transition-colors cursor-pointer"
            >
              <span className="flex items-center gap-1.5">
                {g.group} ({g.factors.length})
                {selCount > 0 && (
                  <span className="font-mono text-[0.6rem] text-primary">{selCount}</span>
                )}
              </span>
              <ChevronRight
                className={cn(
                  "w-3 h-3 text-muted-foreground transition-transform",
                  isOpen && "rotate-90",
                )}
              />
            </button>
            {isOpen && (
              <div className="flex flex-col px-2.5 py-1.5 gap-0.5 bg-background">
                {g.factors.map((f) => {
                  const checked = selectedFactors.has(f.name);
                  const disabled = !checked && disabledFactors.has(f.name);
                  return (
                    <label
                      key={f.name}
                      className={cn(
                        "flex items-center gap-2 py-1 px-1 font-mono text-[0.7rem] rounded cursor-pointer transition-colors hover:bg-secondary",
                        disabled && "opacity-40 cursor-not-allowed hover:bg-transparent",
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => onToggleFactor(f.name)}
                        className="accent-primary"
                      />
                      {f.name}
                    </label>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      <button
        type="button"
        onClick={onCreateFactor}
        className="mt-1.5 inline-flex items-center gap-1 font-mono text-[0.65rem] text-primary hover:underline px-2 py-1.5 cursor-pointer"
      >
        <Plus className="w-3 h-3" />
        新增因子
      </button>
    </section>
  );
}
