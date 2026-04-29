"use client";

import { useState, useEffect, useCallback } from "react";
import { Server, FolderOpen, Shield, Edit3, Save, X } from "lucide-react";
import { apiGet, apiPut } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeIn } from "@/components/motion/FadeIn";
import { EmptyState } from "@/components/EmptyState";
import { Separator } from "@/components/ui/separator";

/* -- Types ---------------------------------------------------------- */

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

/* -- Helpers -------------------------------------------------------- */

function formatUptime(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  return `${h}h ${m}m ${s}s`;
}

/* -- Section card --------------------------------------------------- */

function SectionCard({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-card border overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-3 border-b">
        <span className="text-muted-foreground">{icon}</span>
        <span className="qds-section-label">{title}</span>
      </div>
      <div className="p-5 flex flex-col gap-4">{children}</div>
    </div>
  );
}

/* -- Status dot ----------------------------------------------------- */

function StatusDot({ online }: { online: boolean | undefined }) {
  return (
    <span
      className="inline-block size-2 rounded-full"
      style={{ background: online === undefined ? "var(--t2)" : online ? "var(--suc)" : "var(--dan)" }}
    />
  );
}

/* -- Form input with QDS styling ------------------------------------ */

function FormInput({ label, value, onChange, type = "text" }: {
  label: string; value: string; onChange: (v: string) => void; type?: string;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="qds-stat-label">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 rounded-sm bg-input border px-3 text-[0.72rem] font-mono text-foreground outline-none focus:border-primary focus:shadow-[0_0_0_3px_var(--acc-d)] transition-all"
        style={{ transitionDuration: "var(--dur)" }}
      />
    </div>
  );
}

/* -- Page ----------------------------------------------------------- */

function riskFormFromSettings(rl: RiskLimits): RiskLimits {
  return { max_position_size: rl.max_position_size, max_daily_loss: rl.max_daily_loss, max_order_value: rl.max_order_value ?? 0, max_leverage: rl.max_leverage };
}

export default function SettingsPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editing, setEditing] = useState(false);
  const [riskForm, setRiskForm] = useState<RiskLimits>({ max_position_size: 0, max_daily_loss: 0, max_order_value: 0, max_leverage: 0 });
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, s] = await Promise.all([apiGet<HealthData>("/api/health"), apiGet<SettingsData>("/api/settings")]);
      if (h) setHealth(h);
      if (s) { setSettings(s); if (s.risk_limits) setRiskForm(riskFormFromSettings(s.risk_limits)); }
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

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
    if (settings?.risk_limits) setRiskForm(riskFormFromSettings(settings.risk_limits));
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-5 p-6">
        <div className="flex flex-col gap-0.5">
          <h1 className="text-[1.1rem] font-bold tracking-tight text-foreground">系统设置</h1>
          <span className="qds-stat-label">{"// 加载中..."}</span>
        </div>
        <div className="grid grid-cols-2 gap-5">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="rounded-lg bg-card border p-5">
              <Skeleton className="h-3 w-24 mb-4 bg-secondary" />
              <div className="flex flex-col gap-3">
                {Array.from({ length: 4 }).map((_, j) => <Skeleton key={j} className="h-8 w-full bg-secondary" />)}
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
        <EmptyState variant="error" title="加载失败" description={error} action={{ label: "重试", onClick: loadAll }} />
      </div>
    );
  }

  const uptime = health?.uptime_seconds ?? 0;
  const isHealthy = health?.status === "healthy" || health?.status === "ok";

  const isDirty = editing && settings?.risk_limits && (
    riskForm.max_position_size !== settings.risk_limits.max_position_size ||
    riskForm.max_daily_loss !== settings.risk_limits.max_daily_loss ||
    riskForm.max_order_value !== (settings.risk_limits.max_order_value ?? 0) ||
    riskForm.max_leverage !== settings.risk_limits.max_leverage
  );

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto">
        <div className="flex flex-col gap-6 p-6">
          <div className="flex flex-col gap-0.5 shrink-0">
            <h1 className="text-[1.1rem] font-bold tracking-tight text-foreground">系统设置</h1>
            <span className="qds-stat-label">{`// TinoHelm v${health?.platform_version ?? "0.1.0"}`}</span>
          </div>

          <div className="grid grid-cols-2 gap-5">
            {/* System Info */}
            <FadeIn>
              <SectionCard icon={<Server className="size-4" />} title="系统信息">
                <div className="flex flex-col gap-3">
                  <div className="flex items-center justify-between">
                    <span className="qds-stat-label">系统状态</span>
                    <div className="flex items-center gap-1.5">
                      <StatusDot online={isHealthy} />
                      <span className="text-[0.68rem] font-medium" style={{ color: isHealthy ? "var(--suc)" : "var(--dan)" }}>{isHealthy ? "正常" : "异常"}</span>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="qds-stat-label">PostgreSQL</span>
                    <div className="flex items-center gap-1.5">
                      <StatusDot online={health?.postgres_connected} />
                      <span className="text-[0.68rem] font-medium" style={{ color: health?.postgres_connected ? "var(--suc)" : "var(--t2)" }}>{health?.postgres_connected ? "已连接" : "未连接"}</span>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="qds-stat-label">Redis</span>
                    <div className="flex items-center gap-1.5">
                      <StatusDot online={health?.redis_connected ?? (health?.redis_version !== undefined)} />
                      <span className="text-[0.68rem] font-medium" style={{ color: (health?.redis_connected || health?.redis_version) ? "var(--suc)" : "var(--t2)" }}>{(health?.redis_connected || health?.redis_version) ? "已连接" : "未连接"}</span>
                    </div>
                  </div>
                  <Separator className="bg-border" />
                  {[
                    { label: "版本", value: `TinoHelm v${health?.platform_version ?? "0.1.0"}` },
                    { label: "运行时长", value: formatUptime(uptime) },
                    { label: "Nautilus", value: health?.nautilus_version ?? "—" },
                    { label: "Python", value: health?.python_version ?? "—" },
                    { label: "Redis", value: health?.redis_version ?? "—" },
                    { label: "Sandbox 节点", value: health?.sandbox_node ?? "—" },
                    { label: "Live 节点", value: health?.live_node ?? "—" },
                  ].map((item) => (
                    <div key={item.label} className="flex items-center justify-between">
                      <span className="qds-stat-label">{item.label}</span>
                      <span className="text-[0.68rem] font-mono text-foreground">{item.value}</span>
                    </div>
                  ))}
                </div>
              </SectionCard>
            </FadeIn>

            {/* Risk Limits */}
            <FadeIn delay={0.05}>
              <SectionCard icon={<Shield className="size-4" />} title="风险限额">
                <div className="flex items-center justify-between">
                  <span className="text-[0.68rem] text-muted-foreground">最大持仓/单日亏损/单笔金额/杠杆倍数</span>
                  {!editing ? (
                    <button onClick={() => setEditing(true)} className="flex items-center gap-1 text-[0.62rem] font-semibold text-muted-foreground hover:text-primary transition-colors" style={{ transitionDuration: "var(--dur)" }}>
                      <Edit3 className="size-3" /> 编辑
                    </button>
                  ) : (
                    <div className="flex items-center gap-2">
                      <button onClick={handleCancelEdit} className="flex items-center gap-1 text-[0.62rem] font-semibold text-muted-foreground hover:text-foreground transition-colors"><X className="size-3" /> 取消</button>
                      <button onClick={handleSaveRisk} disabled={saving} className="flex items-center gap-1 text-[0.62rem] font-bold text-primary hover:opacity-80 transition-opacity disabled:opacity-50">
                        {saving ? <span className="size-3 border border-current border-t-transparent rounded-full animate-spin" /> : <Save className="size-3" />} 保存
                      </button>
                    </div>
                  )}
                </div>
                {saveMsg && (
                  <div className="rounded-sm px-3 py-2 text-[0.68rem] font-medium border" style={{
                    background: saveMsg === "保存成功" ? "var(--suc-d)" : "var(--dan-d)",
                    borderColor: `color-mix(in srgb, ${saveMsg === "保存成功" ? "var(--suc)" : "var(--dan)"} 30%, transparent)`,
                    color: saveMsg === "保存成功" ? "var(--suc)" : "var(--dan)",
                  }}>{saveMsg}</div>
                )}
                {settings?.risk_limits === undefined && !editing ? (
                  <span className="text-[0.68rem] text-muted-foreground">暂无风险限额配置</span>
                ) : editing ? (
                  <div className="flex flex-col gap-3">
                    <FormInput label="最大持仓金额 (USD)" type="number" value={String(riskForm.max_position_size)} onChange={(v) => setRiskForm((f) => ({ ...f, max_position_size: parseFloat(v) || 0 }))} />
                    <FormInput label="最大单日亏损 (USD)" type="number" value={String(riskForm.max_daily_loss)} onChange={(v) => setRiskForm((f) => ({ ...f, max_daily_loss: parseFloat(v) || 0 }))} />
                    <FormInput label="最大单笔下单金额 (USD)" type="number" value={String(riskForm.max_order_value)} onChange={(v) => setRiskForm((f) => ({ ...f, max_order_value: parseFloat(v) || 0 }))} />
                    <FormInput label="最大杠杆倍数" type="number" value={String(riskForm.max_leverage)} onChange={(v) => setRiskForm((f) => ({ ...f, max_leverage: parseFloat(v) || 0 }))} />
                  </div>
                ) : (
                  <div className="flex flex-col gap-3">
                    {[
                      { label: "最大持仓金额", value: `$${settings!.risk_limits!.max_position_size.toLocaleString()}` },
                      { label: "最大单日亏损", value: `$${settings!.risk_limits!.max_daily_loss.toLocaleString()}` },
                      { label: "最大单笔下单金额", value: settings!.risk_limits!.max_order_value ? `$${settings!.risk_limits!.max_order_value.toLocaleString()}` : "—" },
                      { label: "最大杠杆倍数", value: `${settings!.risk_limits!.max_leverage}x` },
                    ].map((item) => (
                      <div key={item.label} className="flex items-center justify-between rounded-sm bg-input border px-4 py-2.5">
                        <span className="qds-stat-label">{item.label}</span>
                        <span className="text-[0.68rem] font-mono font-semibold text-foreground">{item.value}</span>
                      </div>
                    ))}
                  </div>
                )}
              </SectionCard>
            </FadeIn>

            {/* Paths */}
            <FadeIn delay={0.1} className="col-span-2">
              <SectionCard icon={<FolderOpen className="size-4" />} title="路径配置">
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
                        <span className="qds-stat-label">{item.label}</span>
                        <div className="flex items-center rounded-sm bg-input border px-4 py-2.5 min-h-[38px]">
                          <span className="text-[0.68rem] font-mono text-qds-t1 break-all">{val ?? "—"}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </SectionCard>
            </FadeIn>
          </div>
        </div>
      </div>

      {/* Save bar */}
      {editing && (
        <div className="shrink-0 flex items-center justify-between px-6 py-3 border-t bg-card" style={{ animation: "fade-up 280ms var(--eo) both" }}>
          <span className="text-[0.68rem] text-muted-foreground">{isDirty ? "有未保存的更改" : saving ? "保存中..." : "编辑模式"}</span>
          <div className="flex items-center gap-2">
            <button onClick={handleCancelEdit} className="qds-btn qds-btn-secondary">取消</button>
            <button onClick={handleSaveRisk} disabled={saving || !isDirty} className="qds-btn qds-btn-primary">{saving ? "保存中..." : "保存更改"}</button>
          </div>
        </div>
      )}
    </div>
  );
}
