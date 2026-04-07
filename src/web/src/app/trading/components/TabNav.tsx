"use client";

import { motion } from "framer-motion";

export interface TabDef {
  id: string;
  label: string;
  shared: boolean;
}

export const TABS: TabDef[] = [
  { id: "overview", label: "概览", shared: true },
  { id: "orders", label: "订单", shared: true },
  { id: "strategies", label: "策略", shared: true },
  { id: "risk", label: "风控", shared: true },
  { id: "market", label: "行情", shared: true },
  { id: "logs", label: "日志", shared: true },
  { id: "compare", label: "回测对比", shared: false },
  { id: "settings", label: "沙盒设置", shared: false },
];

interface Props {
  activeTab: string;
  onTabChange: (id: string) => void;
  nodeType: "sandbox" | "live";
}

export function TabNav({ activeTab, onTabChange, nodeType }: Props) {
  const visibleTabs = TABS.filter((t) => t.shared || nodeType === "sandbox");

  return (
    <div className="shrink-0 flex items-center gap-0 px-4 border-b border bg-background overflow-x-auto">
      {visibleTabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onTabChange(tab.id)}
          className={`relative px-4 py-2.5 text-[11px] font-semibold tracking-wide transition-colors whitespace-nowrap ${activeTab === tab.id ? "text-foreground" : "text-muted-foreground"}`}
        >
          {tab.label}
          {activeTab === tab.id && (
            <motion.div
              layoutId="tab-indicator"
              className="absolute bottom-0 left-2 right-2 h-0.5 rounded-full"
              style={{ backgroundColor: "var(--accent-blue)" }}
              transition={{ type: "spring", stiffness: 400, damping: 35 }}
            />
          )}
        </button>
      ))}
    </div>
  );
}
