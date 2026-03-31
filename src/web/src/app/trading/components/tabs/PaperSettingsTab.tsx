"use client";

import { useEffect, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { toast } from "sonner";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import type { Fill } from "../../page";

interface Props {
  nodeType: "sandbox" | "live";
}

interface PaperConfig {
  starting_capital: number;
  fee_rate: number;
  slippage_model: string;
  latency_ms: number;
}

const DEFAULT_CONFIG: PaperConfig = {
  starting_capital: 10000,
  fee_rate: 0.0004,
  slippage_model: "binance-default",
  latency_ms: 0,
};

const SLIPPAGE_OPTIONS = [
  { value: "none", label: "零费用" },
  { value: "binance-default", label: "Binance 默认" },
  { value: "custom", label: "自定义" },
];

function GlassCard({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-xl border border-white/[0.06] bg-white/[0.02] backdrop-blur-sm overflow-hidden ${className}`}>
      {children}
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/50 mb-3">
      {children}
    </div>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[11px] text-muted-foreground/60 mb-1.5">{children}</div>
  );
}

export function PaperSettingsTab({ nodeType }: Props) {
  const [config, setConfig] = useState<PaperConfig>(DEFAULT_CONFIG);
  const [draft, setDraft] = useState<PaperConfig>(DEFAULT_CONFIG);
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [fills, setFills] = useState<Fill[]>([]);

  const loadConfig = useCallback(async () => {
    try {
      const data = await apiGet<PaperConfig>("/api/node/paper-config", { mode: nodeType });
      if (data) {
        setConfig(data);
        setDraft(data);
      }
    } catch {
      // silent
    }
  }, [nodeType]);

  const loadFills = useCallback(async () => {
    try {
      const data = await apiGet<Fill[]>("/api/trading/fills", {
        node_type: nodeType,
        limit: "200",
      });
      setFills(data ?? []);
    } catch {
      // silent
    }
  }, [nodeType]);

  useEffect(() => {
    loadConfig();
    loadFills();
  }, [loadConfig, loadFills]);

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await fetch(`/api/node/paper-config?mode=${nodeType}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (res.ok) {
        const data = await res.json();
        setConfig(data.config ?? draft);
        toast.success("配置已保存，下次启动生效");
      } else {
        toast.error("保存失败");
      }
    } catch {
      toast.error("保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleLifecycle = async (action: "shutdown" | "start") => {
    try {
      await apiPost("/api/node/lifecycle", { action, mode: nodeType });
      toast.success(action === "shutdown" ? "沙盒已停止" : "沙盒已启动");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "操作失败";
      toast.error(msg);
    }
  };

  const handleReset = async () => {
    if (!confirmReset) {
      setConfirmReset(true);
      return;
    }
    setResetting(true);
    setConfirmReset(false);
    try {
      const res = await fetch(`/api/node/paper-reset?mode=${nodeType}&restart=true`, {
        method: "POST",
      });
      if (res.ok) {
        toast.success("状态已重置，节点正在重启");
        setFills([]);
        await loadConfig();
      } else {
        const data = await res.json().catch(() => ({}));
        toast.error(data?.detail ?? "重置失败");
      }
    } catch {
      toast.error("重置失败");
    } finally {
      setResetting(false);
    }
  };

  // Execution quality stats
  const totalFills = fills.length;
  const limitFills = fills.filter(
    (f) => f.liquidity_side === "MAKER" || f.liquidity_side === "maker"
  );
  const fillRate = totalFills > 0 ? ((limitFills.length / totalFills) * 100).toFixed(1) : "N/A";

  const isDirty =
    draft.starting_capital !== config.starting_capital ||
    draft.fee_rate !== config.fee_rate ||
    draft.slippage_model !== config.slippage_model ||
    draft.latency_ms !== config.latency_ms;

  return (
    <div className="flex flex-col gap-4 p-4 min-h-0">
      {/* Starting Capital */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <SectionLabel>初始资金</SectionLabel>
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <FieldLabel>起始资金 (USDT)</FieldLabel>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground/40 text-[13px]">$</span>
                <input
                  type="number"
                  min={100}
                  step={1000}
                  value={draft.starting_capital}
                  onChange={(e) =>
                    setDraft((d) => ({ ...d, starting_capital: parseFloat(e.target.value) || 0 }))
                  }
                  className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-[13px] font-mono text-foreground focus:outline-none focus:border-[var(--accent-blue)]/50 transition-colors"
                />
              </div>
            </div>
            <div className="text-right">
              <div className="text-[10px] text-muted-foreground/40 mb-1">当前值</div>
              <div className="text-[18px] font-bold font-heading text-[var(--accent-blue)]">
                ${config.starting_capital.toLocaleString()}
              </div>
            </div>
          </div>
        </GlassCard>
      </motion.div>

      {/* Fee Model */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.06, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <SectionLabel>费率模型</SectionLabel>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <FieldLabel>滑点模型</FieldLabel>
              <select
                value={draft.slippage_model}
                onChange={(e) => setDraft((d) => ({ ...d, slippage_model: e.target.value }))}
                className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-[13px] text-foreground focus:outline-none focus:border-[var(--accent-blue)]/50 transition-colors"
              >
                {SLIPPAGE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <FieldLabel>手续费率 (taker, 小数)</FieldLabel>
              <input
                type="number"
                min={0}
                max={0.01}
                step={0.0001}
                value={draft.fee_rate}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, fee_rate: parseFloat(e.target.value) || 0 }))
                }
                className="w-full bg-white/[0.04] border border-white/[0.08] rounded-lg px-3 py-2 text-[13px] font-mono text-foreground focus:outline-none focus:border-[var(--accent-blue)]/50 transition-colors"
              />
            </div>
          </div>
          <div className="mt-2 text-[10px] text-muted-foreground/30">
            Binance 默认: taker 0.04%  maker 0.02%
          </div>
        </GlassCard>
      </motion.div>

      {/* Latency Injection */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.12, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <SectionLabel>延迟注入</SectionLabel>
          <FieldLabel>模拟网络延迟 (ms)</FieldLabel>
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={0}
              max={500}
              step={10}
              value={draft.latency_ms}
              onChange={(e) =>
                setDraft((d) => ({ ...d, latency_ms: parseInt(e.target.value) }))
              }
              className="flex-1 accent-[var(--accent-blue)]"
            />
            <span className="text-[13px] font-mono text-[var(--accent-blue)] w-14 text-right">
              {draft.latency_ms} ms
            </span>
          </div>
          <div className="flex justify-between text-[9px] text-muted-foreground/30 mt-1">
            <span>0 ms</span>
            <span>250 ms</span>
            <span>500 ms</span>
          </div>
        </GlassCard>
      </motion.div>

      {/* Save button */}
      {isDirty && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex justify-end"
        >
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 rounded-lg bg-[var(--accent-blue)]/20 border border-[var(--accent-blue)]/30 text-[var(--accent-blue)] text-[12px] font-semibold hover:bg-[var(--accent-blue)]/30 transition-all disabled:opacity-50"
          >
            {saving ? "保存中..." : "保存配置"}
          </button>
        </motion.div>
      )}

      {/* Quick Iteration */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.18, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <SectionLabel>快速迭代</SectionLabel>
          <div className="flex items-center gap-3">
            <button
              onClick={() => handleLifecycle("shutdown")}
              className="flex-1 py-2.5 rounded-lg bg-white/[0.04] border border-white/[0.08] text-[12px] text-muted-foreground hover:bg-white/[0.08] hover:text-foreground transition-all"
            >
              停止沙盒
            </button>
            <button
              onClick={() => handleLifecycle("start")}
              className="flex-1 py-2.5 rounded-lg bg-[var(--accent-green)]/10 border border-[var(--accent-green)]/20 text-[12px] text-[var(--accent-green)] hover:bg-[var(--accent-green)]/20 transition-all"
            >
              启动沙盒
            </button>
            <button
              onClick={handleReset}
              disabled={resetting}
              className={`flex-1 py-2.5 rounded-lg text-[12px] font-semibold transition-all disabled:opacity-50 ${
                confirmReset
                  ? "bg-red-500/20 border border-red-500/40 text-red-400 animate-pulse"
                  : "bg-white/[0.04] border border-white/[0.08] text-muted-foreground hover:bg-red-500/10 hover:border-red-500/20 hover:text-red-400"
              }`}
            >
              {resetting ? "重置中..." : confirmReset ? "确认重置?" : "状态重置"}
            </button>
          </div>
          {confirmReset && (
            <div className="mt-2 flex items-center justify-between">
              <span className="text-[11px] text-red-400/70">
                将清除所有持仓、成交记录和权益数据，并重启节点
              </span>
              <button
                onClick={() => setConfirmReset(false)}
                className="text-[11px] text-muted-foreground/50 hover:text-muted-foreground ml-3"
              >
                取消
              </button>
            </div>
          )}
          <div className="mt-3 text-[10px] text-muted-foreground/30">
            修改配置后需重启节点生效。状态重置将清除全部 DB 记录和 Redis 状态。
          </div>
        </GlassCard>
      </motion.div>

      {/* Execution Quality */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.24, ease: [0.22, 1, 0.36, 1] }}
      >
        <GlassCard className="p-4">
          <SectionLabel>执行质量分析</SectionLabel>
          {totalFills === 0 ? (
            <div className="flex items-center justify-center py-8 text-muted-foreground/30 text-[12px]">
              暂无成交记录
            </div>
          ) : (
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-white/[0.02] rounded-lg p-3">
                <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/40 mb-2">
                  总成交笔数
                </div>
                <div className="text-[22px] font-bold font-heading text-foreground">
                  {totalFills}
                </div>
              </div>
              <div className="bg-white/[0.02] rounded-lg p-3">
                <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/40 mb-2">
                  Maker 成交
                </div>
                <div className="text-[22px] font-bold font-heading text-[var(--accent-green)]">
                  {limitFills.length}
                </div>
              </div>
              <div className="bg-white/[0.02] rounded-lg p-3">
                <div className="text-[10px] font-semibold tracking-[1.5px] uppercase text-muted-foreground/40 mb-2">
                  Maker 比例
                </div>
                <div className="text-[22px] font-bold font-heading text-[var(--accent-blue)]">
                  {fillRate === "N/A" ? "N/A" : `${fillRate}%`}
                </div>
              </div>
            </div>
          )}
        </GlassCard>
      </motion.div>
    </div>
  );
}
