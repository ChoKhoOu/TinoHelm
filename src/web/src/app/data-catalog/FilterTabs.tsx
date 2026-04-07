"use client";

import { FILTER_GROUPS } from "./types";

interface FilterTabsProps {
  totalCount: number;
  typeCounts: Record<string, number>;
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
  activeGroup,
  activeSub,
  onGroupChange,
  onSubChange,
}: FilterTabsProps) {
  const activeGroupDef = FILTER_GROUPS[activeGroup];
  const subTypes = activeGroupDef?.types ?? null;
  const showSubFilter = subTypes !== null && subTypes.length > 1;

  return (
    <>
      <div className="dc-filter-strip">
        {Object.entries(FILTER_GROUPS).map(([key, group]) => {
          const count = getGroupCount(key, totalCount, typeCounts);
          const isActive = activeGroup === key;
          return (
            <div
              key={key}
              className={`dc-filter-item${isActive ? " active" : ""}`}
              onClick={() => {
                onGroupChange(key);
                onSubChange(null);
              }}
            >
              <span
                className="dc-filter-dot"
                style={{ background: group.dot }}
              />
              {group.label}
              <span className="dc-filter-count">{count}</span>
            </div>
          );
        })}
      </div>

      {showSubFilter && (
        <div className="dc-sub-filter">
          <div
            className={`dc-sub-item${activeSub === null ? " active" : ""}`}
            onClick={() => onSubChange(null)}
          >
            全部
          </div>
          {subTypes!.map((type) => (
            <div
              key={type}
              className={`dc-sub-item${activeSub === type ? " active" : ""}`}
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
