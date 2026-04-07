"use client";

import { CATEGORY_LABELS } from "./types";

interface FilterTabsProps {
  categories: string[];
  activeCategory: string | null;
  onCategoryChange: (cat: string | null) => void;
}

export function FilterTabs({ categories, activeCategory, onCategoryChange }: FilterTabsProps) {
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <button
        onClick={() => onCategoryChange(null)}
        className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all ${
          activeCategory === null
            ? "bg-qds-info-dim text-qds-info"
            : "text-muted-foreground hover:text-foreground hover:bg-input"
        }`}
      >
        全部
      </button>
      {categories.map((cat) => (
        <button
          key={cat}
          onClick={() => onCategoryChange(cat)}
          className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all ${
            activeCategory === cat
              ? "bg-qds-info-dim text-qds-info"
              : "text-muted-foreground hover:text-foreground hover:bg-input"
          }`}
        >
          {CATEGORY_LABELS[cat] || cat}
        </button>
      ))}
    </div>
  );
}
