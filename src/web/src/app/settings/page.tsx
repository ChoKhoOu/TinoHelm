"use client";

import { useState, useEffect } from "react";
import { Lock, Bell, Info } from "lucide-react";
import { Toggle } from "@/components/ui/Toggle";
import { apiGet } from "@/lib/api";
import { useI18n } from "@/i18n";

/* ── Types ──────────────────────────────────────────────── */

interface ApiKeyStatus { label: string; configured: boolean; source: string }

/* ── Section Card Wrapper ──────────────────────────────────── */

function SectionCard({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-[14px]">
        <span className="text-[var(--text-muted)]">{icon}</span>
        <span className="text-[11px] font-semibold tracking-[0.5px] text-[var(--text-secondary)]">
          {title}
        </span>
      </div>
      <div className="h-px bg-[var(--border-gray)]" />
      <div className="p-5 flex flex-col gap-4">{children}</div>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────── */

export default function SettingsPage() {
  const { t } = useI18n();
  const [systemInfo, setSystemInfo] = useState<{label: string; value: string}[]>([]);
  const [riskLimits, setRiskLimits] = useState<{label: string; value: string; key: string}[]>([]);
  const [apiKeyStatus, setApiKeyStatus] = useState<ApiKeyStatus[]>([
    { label: "BINANCE API KEY", configured: false, source: "env" },
    { label: "BINANCE SECRET", configured: false, source: "env" },
  ]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [notifications, setNotifications] = useState({
    email: true,
    push: false,
    sound: true,
    killSwitch: true,
  });

  useEffect(() => {
    let cancelled = false;
    async function loadSettings() {
      try {
        const [health, settings] = await Promise.all([
          apiGet<{
            status: string;
            nautilus_version?: string;
            python_version?: string;
            redis_version?: string;
            uptime_seconds?: number;
            platform_version?: string;
            binance_connected?: boolean;
          }>("/api/health"),
          apiGet<{
            risk_limits?: { max_position_size: number; max_daily_loss: number; max_leverage: number };
          }>("/api/settings"),
        ]);
        if (cancelled) return;
        if (health) {
          const uptime = health.uptime_seconds ?? 0;
          const hours = Math.floor(uptime / 3600);
          const mins = Math.floor((uptime % 3600) / 60);
          const secs = Math.floor(uptime % 60);
          setSystemInfo([
            { label: "NAUTILUS VERSION", value: health.nautilus_version ?? "unknown" },
            { label: "PYTHON", value: health.python_version ?? "unknown" },
            { label: "REDIS", value: health.redis_version ?? "unknown" },
            { label: "UPTIME", value: `${hours}h ${mins}m ${secs}s` },
            { label: "PLATFORM", value: `TinoHelm v${health.platform_version ?? "0.1.0"}` },
          ]);
          const connected = health.binance_connected ?? health.status === "healthy";
          setApiKeyStatus([
            { label: "BINANCE API KEY", configured: connected, source: "env: BINANCE_API_KEY" },
            { label: "BINANCE SECRET", configured: connected, source: "env: BINANCE_API_SECRET" },
          ]);
        }
        if (settings?.risk_limits) {
          const rl = settings.risk_limits;
          setRiskLimits([
            { label: "MAX POSITION SIZE", value: `$${rl.max_position_size.toLocaleString()}`, key: "max_position_size" },
            { label: "MAX DAILY LOSS", value: `$${rl.max_daily_loss.toLocaleString()}`, key: "max_daily_loss" },
            { label: "MAX LEVERAGE", value: `${rl.max_leverage}x`, key: "max_leverage" },
          ]);
        }
      } catch {
        if (!cancelled) setError("common.loadFailed");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadSettings();
    return () => { cancelled = true; };
  }, []);

  const toggleNotification = (key: keyof typeof notifications) => {
    setNotifications((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <span className="font-mono text-[12px] text-[var(--accent-red)]">{t("common.loadFailed")}</span>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col h-full p-6 gap-5">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("settings.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("settings.loading")}
          </span>
        </div>
        <div className="flex items-center justify-center min-h-[300px]">
          <div className="flex flex-col items-center gap-3">
            <div className="w-6 h-6 border-2 border-[var(--accent-green)] border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-[var(--text-muted)]">{t("settings.loading")}</span>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full p-6 gap-5">
      {/* Title */}
      <div className="flex flex-col gap-1">
        <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
          {t("settings.title")}
        </h1>
        <span className="text-[11px] font-medium text-[var(--text-muted)]">
          {t("settings.subtitle")}
        </span>
      </div>

      {/* Two-column layout */}
      <div className="flex gap-5 flex-1 min-h-0">
        {/* Left column */}
        <div className="flex-1 flex flex-col gap-5">
          {/* API Keys */}
          <SectionCard
            icon={<Lock className="w-4 h-4" />}
            title={t("settings.apiKeys")}
          >
            {apiKeyStatus.map((key) => (
              <div key={key.label} className="flex flex-col gap-1.5">
                <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
                  {key.label}
                </span>
                <div className="flex items-center justify-between rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] px-[14px] py-[10px]">
                  <span className={`text-[11px] font-medium ${key.configured ? "text-[var(--accent-green)]" : "text-[var(--text-muted)]"}`}>
                    {key.configured ? t("settings.configured") : t("settings.notConfigured")}
                  </span>
                  <span className="text-[10px] font-mono text-[var(--text-muted)]">
                    {key.source}
                  </span>
                </div>
              </div>
            ))}
          </SectionCard>

          {/* Risk Limits */}
          <SectionCard
            icon={
              <svg
                className="w-4 h-4"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M8 1L14.5 13H1.5L8 1Z" />
                <path d="M8 6V9" />
                <circle cx="8" cy="11" r="0.5" fill="currentColor" />
              </svg>
            }
            title={t("settings.riskLimits")}
          >
            {riskLimits.length > 0 ? (
              riskLimits.map((item) => (
                <div key={item.key} className="flex flex-col gap-1.5">
                  <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
                    {item.label}
                  </span>
                  <div className="flex items-center rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] px-[14px] py-[10px]">
                    <span className="text-[11px] font-medium text-[var(--text-primary)]">
                      {item.value}
                    </span>
                  </div>
                </div>
              ))
            ) : (
              <span className="text-[11px] text-[var(--text-muted)]">
                {t("settings.noRiskLimits")}
              </span>
            )}
          </SectionCard>
        </div>

        {/* Right column */}
        <div className="w-[380px] flex flex-col gap-5">
          {/* Notifications */}
          <SectionCard
            icon={<Bell className="w-4 h-4" />}
            title={t("settings.notifications")}
          >
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                {t("settings.emailAlerts")}
              </span>
              <Toggle
                checked={notifications.email}
                onChange={() => toggleNotification("email")}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                {t("settings.pushNotifications")}
              </span>
              <Toggle
                checked={notifications.push}
                onChange={() => toggleNotification("push")}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                {t("settings.soundOnFill")}
              </span>
              <Toggle
                checked={notifications.sound}
                onChange={() => toggleNotification("sound")}
              />
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                {t("settings.killSwitchSms")}
              </span>
              <Toggle
                checked={notifications.killSwitch}
                onChange={() => toggleNotification("killSwitch")}
              />
            </div>
          </SectionCard>

          {/* System Info */}
          <SectionCard
            icon={<Info className="w-4 h-4" />}
            title={t("settings.systemInfo")}
          >
            {systemInfo.length > 0 ? (
              systemInfo.map((item) => (
                <div key={item.label} className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
                    {item.label}
                  </span>
                  <span className="text-[11px] font-medium text-[var(--text-primary)]">
                    {item.value}
                  </span>
                </div>
              ))
            ) : (
              <span className="text-[11px] text-[var(--text-muted)]">
                {t("settings.noSystemInfo")}
              </span>
            )}
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
