"use client";

import { FILTER_GROUPS } from "./types";

interface FilterTabsProps {
  totalCount: number;
  typeCounts: Record<string, number>;
  subTypeCounts: Record<string, number>;
  activeGroup: string;
  activeSub: string | null;
  onGroupChange: (group: string) => void;
  onSubChange: (sub: string | null) => void;
}

function getGroupCount(groupKey: string, totalCount: number, typeCounts: Record<string, number>): number {
  const group = FILTER_GROUPS[groupKey];
  if (!group || group.types === null) return totalCount;
  return group.types.reduce((sum, t) => sum + (typeCounts[t] ?? 0), 0);
}

export function FilterTabs({
  totalCount,
  typeCounts,
  subTypeCounts,
  activeGroup,
  activeSub,
  onGroupChange,
  onSubChange,
}: FilterTabsProps) {
  const subTypes = Object.keys(subTypeCounts).sort();
  const showSubFilter = subTypes.length > 1;

  return (
    <>
      <div className="flex flex-wrap gap-5 mb-2 font-mono text-[.72rem]">
        {Object.entries(FILTER_GROUPS).map(([key, group]) => {
          const count = getGroupCount(key, totalCount, typeCounts);
          const isActive = activeGroup === key;
          return (
            <div
              key={key}
              className={`flex items-center gap-1.5 cursor-pointer py-1.5 border-b-2 transition-all duration-150 ${
                isActive
                  ? "text-foreground border-primary"
                  : "text-muted-foreground border-transparent hover:text-foreground"
              }`}
              onClick={() => {
                onGroupChange(key);
                onSubChange(null);
              }}
            >
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ background: group.dot }}
              />
              {group.label}
              <span className={`text-[.62rem] ${isActive ? "text-primary" : "text-qds-t3"}`}>{count}</span>
            </div>
          );
        })}
      </div>

      {showSubFilter && (
        <div
          className="flex gap-4 mb-5 font-mono text-[.68rem] pl-4 border-l-2 border-border ml-1"
          style={{ animation: "none" }}
        >
          <div
            className={`cursor-pointer py-1 px-2 rounded transition-all duration-150 ${
              activeSub === null
                ? "text-primary bg-qds-accent-dim"
                : "text-muted-foreground hover:text-foreground hover:bg-secondary"
            }`}
            onClick={() => onSubChange(null)}
          >
            全部
          </div>
          {subTypes.map((type) => (
            <div
              key={type}
              className={`cursor-pointer py-1 px-2 rounded transition-all duration-150 ${
                activeSub === type
                  ? "text-primary bg-qds-accent-dim"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary"
              }`}
              onClick={() => onSubChange(type)}
            >
              {type}
            </div>
          ))}
        </div>
      )}
    </>
  );
}
