"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import {
  LayoutDashboard,
  Cpu,
  FlaskConical,
  Activity,
  Wallet,
  ListOrdered,
  ChartNoAxesColumn,
  Settings,
  Database,
  Eye,
} from "lucide-react";
import { useI18n } from "@/i18n";
import type { TranslationKey } from "@/i18n";

const navItems: { href: string; labelKey: TranslationKey; icon: typeof LayoutDashboard }[] = [
  { href: "/", labelKey: "nav.dashboard", icon: LayoutDashboard },
  { href: "/strategies", labelKey: "nav.strategies", icon: Cpu },
  { href: "/backtest", labelKey: "nav.backtest", icon: FlaskConical },
  { href: "/live", labelKey: "nav.live", icon: Activity },
  { href: "/portfolio", labelKey: "nav.portfolio", icon: Wallet },
  { href: "/orders", labelKey: "nav.orders", icon: ListOrdered },
  { href: "/watchlist", labelKey: "nav.watchlist", icon: Eye },
  { href: "/analytics", labelKey: "nav.analytics", icon: ChartNoAxesColumn },
  { href: "/data-catalog", labelKey: "nav.dataCatalog", icon: Database },
];

function navLinkClasses(isActive: boolean) {
  return `flex items-center gap-3 rounded-lg px-3 py-[10px] text-xs font-medium tracking-[0.5px] transition-colors duration-150 ${
    isActive
      ? "bg-[var(--accent-green-10)] text-[var(--accent-green)] font-semibold"
      : "text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-subtle)]"
  }`;
}

export function Sidebar() {
  const pathname = usePathname();
  const { locale, setLocale, t } = useI18n();

  return (
    <aside className="hidden md:flex w-[240px] shrink-0 flex-col justify-between bg-[var(--bg-sidebar)] border-r border-[var(--border-gray)] h-full">
      <div className="flex flex-col gap-2">
        {/* Logo */}
        <div className="flex items-center gap-[10px] px-5 py-6">
          <div className="w-7 h-7 rounded-lg bg-[var(--accent-green)]" />
          <span className="font-heading text-lg font-bold tracking-tight text-[var(--text-primary)]">
            TinoHelm
          </span>
        </div>
        {/* Nav */}
        <nav className="flex flex-col gap-0.5 px-3">
          {navItems.map((item) => {
            const isActive = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={navLinkClasses(isActive)}
              >
                <item.icon className="w-4 h-4" />
                {t(item.labelKey)}
              </Link>
            );
          })}
        </nav>
      </div>
      {/* Bottom */}
      <div className="flex flex-col gap-2 px-3 pb-4">
        <Link
          href="/settings"
          className={navLinkClasses(pathname === "/settings")}
        >
          <Settings className="w-4 h-4" />
          {t("nav.settings")}
        </Link>
        {/* Language Switcher */}
        <div className="px-1 py-2 border-t border-[var(--border-gray)]">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setLocale("en")}
              className={`px-2 py-1 rounded text-[10px] font-bold tracking-wide transition-colors ${
                locale === "en"
                  ? "bg-[var(--accent-green-20)] text-[var(--accent-green)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
              }`}
            >
              EN
            </button>
            <button
              onClick={() => setLocale("zh")}
              className={`px-2 py-1 rounded text-[10px] font-bold tracking-wide transition-colors ${
                locale === "zh"
                  ? "bg-[var(--accent-green-20)] text-[var(--accent-green)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text-secondary)]"
              }`}
            >
              中文
            </button>
          </div>
        </div>
        <div className="h-px bg-[var(--border-gray)]" />
        <div className="flex items-center gap-3 px-2 py-[10px]">
          <div className="w-8 h-8 rounded-full bg-[var(--bg-elevated)] flex items-center justify-center">
            <span className="text-[11px] font-bold text-[var(--accent-green)]">
              TH
            </span>
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-xs font-semibold text-[var(--text-primary)]">
              TinoHelm
            </span>
            <span className="text-[10px] font-medium text-[var(--text-muted)]">
              // ADMIN
            </span>
          </div>
        </div>
      </div>
    </aside>
  );
}
