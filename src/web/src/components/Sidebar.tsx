"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  LayoutDashboard, TrendingUp, FlaskConical, Cpu, Database,
  BarChart3, ListOrdered, Eye, Zap, Settings,
} from "lucide-react";
import {
  Tooltip, TooltipContent, TooltipTrigger,
} from "@/components/ui/tooltip";

const navItems = [
  { href: "/", label: "仪表盘", icon: LayoutDashboard },
  { href: "/trading", label: "交易", icon: TrendingUp },
  { href: "/backtest", label: "回测", icon: FlaskConical },
  { href: "/strategies", label: "策略", icon: Cpu },
  { href: "/data-catalog", label: "数据", icon: Database },
  { href: "/analytics", label: "分析", icon: BarChart3 },
  { href: "/orders", label: "订单", icon: ListOrdered },
  { href: "/watchlist", label: "观察", icon: Eye },
  { href: "/optimization", label: "优化", icon: Zap },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex flex-col items-center justify-between w-16 shrink-0 h-full bg-sidebar border-r border-border">
      <div className="flex flex-col items-center w-full">
        {/* Logo */}
        <div className="flex items-center justify-center w-full h-14 mb-2">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
            <span className="text-[11px] font-bold text-white">TH</span>
          </div>
        </div>

        <div className="w-8 h-px bg-border mb-2" />

        {/* Nav */}
        <nav className="flex flex-col items-center gap-1 w-full px-2">
          {navItems.map((item) => {
            const isActive = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));
            return (
              <Tooltip key={item.href}>
                <TooltipTrigger
                  render={
                    <Link
                      href={item.href}
                      className={`relative flex items-center justify-center w-10 h-10 rounded-lg transition-colors duration-150 ${
                        isActive
                          ? "text-primary bg-[var(--accent-blue-20)]"
                          : "text-muted-foreground hover:text-muted-foreground hover:bg-muted"
                      }`}
                    />
                  }
                >
                  {isActive && (
                    <motion.div
                      layoutId="sidebar-active"
                      className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-primary"
                      transition={{ type: "spring", stiffness: 350, damping: 30 }}
                    />
                  )}
                  <item.icon className="w-[18px] h-[18px]" />
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={8}>
                  <p className="text-xs font-medium">{item.label}</p>
                </TooltipContent>
              </Tooltip>
            );
          })}
        </nav>
      </div>

      {/* Bottom — Settings */}
      <div className="flex flex-col items-center gap-2 pb-4 px-2 w-full">
        <div className="w-8 h-px bg-border" />
        <Tooltip>
          <TooltipTrigger
            render={
              <Link
                href="/settings"
                className={`flex items-center justify-center w-10 h-10 rounded-lg transition-colors duration-150 ${
                  pathname === "/settings"
                    ? "text-primary bg-[var(--accent-blue-20)]"
                    : "text-muted-foreground hover:text-muted-foreground hover:bg-muted"
                }`}
              />
            }
          >
            <Settings className="w-[18px] h-[18px]" />
          </TooltipTrigger>
          <TooltipContent side="right" sideOffset={8}>
            <p className="text-xs font-medium">设置</p>
          </TooltipContent>
        </Tooltip>
      </div>
    </aside>
  );
}
