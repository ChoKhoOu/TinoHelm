"use client";

import { useState, useEffect } from "react";
import { Server, FolderOpen, Shield, Edit3, Save, X } from "lucide-react";
import { apiGet, apiPut } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeIn } from "@/components/motion/FadeIn";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

/* ── Types ───────────────────────────────────────────────────────── */

interface HealthData {
  status: string;
  nautilus_version?: string;
  python_version?: string;
  redis_version?: string;
  uptime_seconds?: number;
  platform_version?: string;
  postgres_connected?: boolean;
  redis_connected?: boolean;
  sandbox_node?: string;
  live_node?: string;
}

interface RiskLimits {
  max_position_size: number;
  max_daily_loss: number;
  max_order_value: number;
  max_leverage: number;
}

interface SettingsData {
  risk_limits?: RiskLimits;
  strategies_dir?: string;
  data_dir?: string;
  artifacts_dir?: string;
  config_dir?: string;
}

/* ── Helpers ─────────────────────────────────────────────────────── */

function formatUptime(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return `${h}h ${m}m ${s}s`;
}

/* ── Section card ────────────────────────────────────────────────── */

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
    <div className="rounded-xl bg-card border border-border overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-3 border-b border-border">
        <span className="text-muted-foreground">{icon}</span>
        <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
          {title}
        </span>
      </div>
      <div className="p-5 flex flex-col gap-4">{children}</div>
    </div>
  );
}

/* ── Status dot ──────────────────────────────────────────────────── */

function StatusDot({ online }: { online: boolean | undefined }) {
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${
        online === undefined
          ? "bg-muted-foreground"
          : online
          ? "bg-[var(--accent-green)]"
          : "bg-[var(--accent-red)]"
      }`}
    />
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function SettingsPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Risk limits edit state
  const [editing, setEditing] = useState(false);
  const [riskForm, setRiskForm] = useState<RiskLimits>({
    max_position_size: 0,
    max_daily_loss: 0,
    max_order_value: 0,
    max_leverage: 0,
  });
  const [saving, setSaving] = useState(false);

  function riskFormFromSettings(rl: RiskLimits): RiskLimits {
    return {
      max_position_size: rl.max_position_size,
      max_daily_loss: rl.max_daily_loss,
      max_order_value: rl.max_order_value ?? 0,
      max_leverage: rl.max_leverage,
    };
  }
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  useEffect(() => {
    loadAll();
  }, []);

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [h, s] = await Promise.all([
        apiGet<HealthData>("/api/health"),
        apiGet<SettingsData>("/api/settings"),
      ]);
      if (h) setHealth(h);
      if (s) {
        setSettings(s);
        if (s.risk_limits) {
          setRiskForm(riskFormFromSettings(s.risk_limits));
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveRisk() {
    setSaving(true);
    setSaveMsg(null);
    try {
      await apiPut("/api/settings/risk-limits", riskForm);
      setSaveMsg("保存成功");
      setEditing(false);
      await loadAll();
    } catch (err) {
      setSaveMsg(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  function handleCancelEdit() {
    setEditing(false);
    setSaveMsg(null);
    if (settings?.risk_limits) {
      setRiskForm(riskFormFromSettings(settings.risk_limits));
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-5 p-6">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-heading text-[22px] font-bold tracking-tight text-foreground">
            系统设置
          </h1>
          <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
            // 加载中...
          </span>
        </div>
        <div className="grid grid-cols-2 gap-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-xl bg-card border border-border p-5">
              <Skeleton className="h-3 w-24 mb-4 bg-popover" />
              <div className="flex flex-col gap-3">
                {Array.from({ length: 4 }).map((_, j) => (
                  <Skeleton key={j} className="h-8 w-full bg-popover" />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-8">
        <span className="text-[11px] text-destructive">{error}</span>
      </div>
    );
  }

  const uptime = health?.uptime_seconds ?? 0;
  const isHealthy = health?.status === "healthy" || health?.status === "ok";

  return (
    <div className="flex flex-col gap-6 p-6 h-full overflow-y-auto">
      {/* Title */}
      <div className="flex flex-col gap-0.5 shrink-0">
        <h1 className="font-heading text-[22px] font-bold tracking-tight text-foreground">
          系统设置
        </h1>
        <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
          // TinoHelm v{health?.platform_version ?? "0.1.0"}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-5">
        {/* ── Section 1: 系统信息 ─────────────────────────────── */}
        <FadeIn>
          <SectionCard icon={<Server className="w-4 h-4" />} title="系统信息">
            {/* Health indicators */}
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
                  系统状态
                </span>
                <div className="flex items-center gap-1.5">
                  <StatusDot online={isHealthy} />
                  <span
                    className={`text-[11px] font-medium ${
                      isHealthy ? "text-[var(--accent-green)]" : "text-[var(--accent-red)]"
                    }`}
                  >
                    {isHealthy ? "正常" : "异常"}
                  </span>
                </div>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
                  PostgreSQL
                </span>
                <div className="flex items-center gap-1.5">
                  <StatusDot online={health?.postgres_connected} />
                  <span
                    className={`text-[11px] font-medium ${
                      health?.postgres_connected
                        ? "text-[var(--accent-green)]"
                        : "text-muted-foreground"
                    }`}
                  >
                    {health?.postgres_connected ? "已连接" : "未连接"}
                  </span>
                </div>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
                  Redis
                </span>
                <div className="flex items-center gap-1.5">
                  <StatusDot online={health?.redis_connected ?? (health?.redis_version !== undefined)} />
                  <span
                    className={`text-[11px] font-medium ${
                      health?.redis_connected || health?.redis_version
                        ? "text-[var(--accent-green)]"
                        : "text-muted-foreground"
                    }`}
                  >
                    {health?.redis_connected || health?.redis_version ? "已连接" : "未连接"}
                  </span>
                </div>
              </div>

              <Separator />

              {[
                { label: "版本", value: `TinoHelm v${health?.platform_version ?? "0.1.0"}` },
                { label: "运行时长", value: formatUptime(uptime) },
                { label: "Nautilus", value: health?.nautilus_version ?? "—" },
                { label: "Python", value: health?.python_version ?? "—" },
                { label: "Redis", value: health?.redis_version ?? "—" },
                {
                  label: "Sandbox 节点",
                  value: health?.sandbox_node ?? "—",
                },
                {
                  label: "Live 节点",
                  value: health?.live_node ?? "—",
                },
              ].map((item) => (
                <div key={item.label} className="flex items-center justify-between">
                  <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
                    {item.label}
                  </span>
                  <span className="text-[11px] font-mono text-foreground">
                    {item.value}
                  </span>
                </div>
              ))}
            </div>
          </SectionCard>
        </FadeIn>

        {/* ── Section 2: 风险限额 ─────────────────────────────── */}
        <FadeIn delay={0.05}>
          <SectionCard icon={<Shield className="w-4 h-4" />} title="风险限额">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">最大持仓/单日亏损/单笔金额/杠杆倍数</span>
              {!editing ? (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditing(true)}
                  className="gap-1 text-[10px] font-semibold text-muted-foreground hover:text-[var(--accent-green)] transition-colors"
                >
                  <Edit3 className="w-3 h-3" />
                  编辑
                </Button>
              ) : (
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleCancelEdit}
                    className="gap-1 text-[10px] font-semibold text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X className="w-3 h-3" />
                    取消
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={handleSaveRisk}
                    disabled={saving}
                    className="gap-1 text-[10px] font-bold text-[var(--accent-green)] hover:opacity-80 transition-opacity disabled:opacity-50"
                  >
                    {saving ? (
                      <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <Save className="w-3 h-3" />
                    )}
                    保存
                  </Button>
                </div>
              )}
            </div>

            {saveMsg && (
              <div
                className={`rounded-lg px-3 py-2 text-[11px] font-medium border ${
                  saveMsg === "保存成功"
                    ? "bg-[var(--accent-green-20)] border-[var(--accent-green)]/30 text-[var(--accent-green)]"
                    : "bg-[var(--accent-red-20)] border-[var(--accent-red)]/30 text-[var(--accent-red)]"
                }`}
              >
                {saveMsg}
              </div>
            )}

            {settings?.risk_limits === undefined && !editing ? (
              <span className="text-[11px] text-muted-foreground">暂无风险限额配置</span>
            ) : editing ? (
              <div className="flex flex-col gap-3">
                <Input
                  label="最大持仓金额 (USD)"
                  type="number"
                  value={String(riskForm.max_position_size)}
                  onChange={(e) =>
                    setRiskForm((f) => ({
                      ...f,
                      max_position_size: parseFloat(e.target.value) || 0,
                    }))
                  }
                />
                <Input
                  label="最大单日亏损 (USD)"
                  type="number"
                  value={String(riskForm.max_daily_loss)}
                  onChange={(e) =>
                    setRiskForm((f) => ({
                      ...f,
                      max_daily_loss: parseFloat(e.target.value) || 0,
                    }))
                  }
                />
                <Input
                  label="最大单笔下单金额 (USD)"
                  type="number"
                  value={String(riskForm.max_order_value)}
                  onChange={(e) =>
                    setRiskForm((f) => ({
                      ...f,
                      max_order_value: parseFloat(e.target.value) || 0,
                    }))
                  }
                />
                <Input
                  label="最大杠杆倍数"
                  type="number"
                  value={String(riskForm.max_leverage)}
                  onChange={(e) =>
                    setRiskForm((f) => ({
                      ...f,
                      max_leverage: parseFloat(e.target.value) || 0,
                    }))
                  }
                />
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {[
                  {
                    label: "最大持仓金额",
                    value: `$${settings!.risk_limits!.max_position_size.toLocaleString()}`,
                  },
                  {
                    label: "最大单日亏损",
                    value: `$${settings!.risk_limits!.max_daily_loss.toLocaleString()}`,
                  },
                  {
                    label: "最大单笔下单金额",
                    value: settings!.risk_limits!.max_order_value
                      ? `$${settings!.risk_limits!.max_order_value.toLocaleString()}`
                      : "—",
                  },
                  {
                    label: "最大杠杆倍数",
                    value: `${settings!.risk_limits!.max_leverage}x`,
                  },
                ].map((item) => (
                  <div
                    key={item.label}
                    className="flex items-center justify-between rounded-lg bg-popover border border-border px-4 py-2.5"
                  >
                    <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
                      {item.label}
                    </span>
                    <span className="text-[11px] font-mono font-semibold text-foreground">
                      {item.value}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </SectionCard>
        </FadeIn>

        {/* ── Section 3: 路径配置 (只读) ──────────────────────── */}
        <FadeIn delay={0.1} className="col-span-2">
          <SectionCard icon={<FolderOpen className="w-4 h-4" />} title="路径配置">
            <div className="grid grid-cols-2 gap-4">
              {[
                { label: "策略目录", key: "strategies_dir" },
                { label: "数据目录", key: "data_dir" },
                { label: "结果目录", key: "artifacts_dir" },
                { label: "配置目录", key: "config_dir" },
              ].map((item) => {
                const val = settings?.[item.key as keyof SettingsData] as string | undefined;
                return (
                  <div key={item.key} className="flex flex-col gap-1.5">
                    <span className="text-[10px] font-semibold tracking-[0.5px] text-muted-foreground uppercase">
                      {item.label}
                    </span>
                    <div className="flex items-center rounded-lg bg-popover border border-border px-4 py-2.5 min-h-[38px]">
                      <span className="text-[11px] font-mono text-muted-foreground break-all">
                        {val ?? "—"}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </SectionCard>
        </FadeIn>
      </div>
    </div>
  );
}
