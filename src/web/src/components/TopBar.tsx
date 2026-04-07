"use client";

import { usePathname } from "next/navigation";
import { Search } from "lucide-react";
import { ThemeToggle } from "@/components/ThemeToggle";

const pathTitles: Record<string, string> = {
  "/": "Dashboard",
  "/trading": "Trading Terminal",
  "/backtest": "Backtests",
  "/strategies": "Strategies",
  "/data-catalog": "Data Catalog",
  "/analytics": "Analytics",
  "/orders": "Orders",
  "/watchlist": "Watchlist",
  "/optimization": "Optimization",
  "/settings": "Settings",
};

export function TopBar() {
  const pathname = usePathname();

  const title =
    pathTitles[pathname] ??
    Object.entries(pathTitles).find(
      ([k]) => k !== "/" && pathname.startsWith(k),
    )?.[1] ??
    "TinoHelm";

  return (
    <header
      className="flex items-center justify-between shrink-0 px-6 border-b bg-background"
      style={{ height: 48, minHeight: 48 }}
    >
      {/* Breadcrumb */}
      <div className="font-mono text-[0.75rem] text-muted-foreground flex items-center gap-1.5">
        <span>TinoHelm</span>
        <span className="opacity-40">/</span>
        <span className="text-foreground font-medium">{title}</span>
      </div>

      {/* Right actions */}
      <div className="flex items-center gap-4">
        {/* Search */}
        <button
          className="font-mono text-[0.72rem] px-3 py-1 border rounded-sm bg-transparent text-muted-foreground cursor-pointer flex items-center gap-1.5 hover:border-qds-border-hover hover:text-foreground transition-all duration-150"
        >
          <Search className="w-3.5 h-3.5" />
          Search
          <kbd className="text-[0.6rem] bg-secondary px-1.5 py-0.5 rounded-[3px]">
            ⌘K
          </kbd>
        </button>

        {/* Theme toggle */}
        <ThemeToggle />
      </div>
    </header>
  );
}
