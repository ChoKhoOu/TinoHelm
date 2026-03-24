"use client";

import { useState, useEffect, useCallback } from "react";
import { Plus, Trash2, Star } from "lucide-react";
import { apiGet, apiPost, apiDelete } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { FadeIn } from "@/components/motion/FadeIn";
import { StaggerContainer, StaggerItem } from "@/components/motion/StaggerContainer";

/* ── Types ──────────────────────────────────────────────────── */

interface WatchlistItem {
  id: number;
  instrument_id: string;
  source: string;
  created_at: string | null;
}

/* ── Dialog ─────────────────────────────────────────────────── */

function AddDialog({
  open,
  onClose,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  onAdd: (symbol: string, source: string) => Promise<void>;
}) {
  const [symbol, setSymbol] = useState("");
  const [source, setSource] = useState("BINANCE");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open) { setSymbol(""); setSource("BINANCE"); setErr(""); }
  }, [open]);

  async function submit() {
    if (!symbol.trim()) { setErr("请输入交易对"); return; }
    setBusy(true);
    setErr("");
    try {
      await onAdd(symbol.trim(), source.trim() || "BINANCE");
      onClose();
    } catch {
      setErr("添加失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-[400px] rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-gray)]">
          <span className="text-[13px] font-bold text-[var(--text-primary)]">添加品种</span>
          <button
            onClick={onClose}
            className="text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Body */}
        <div className="p-5 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-[10px] font-semibold tracking-wide text-[var(--text-muted)] uppercase font-mono">
              交易对
            </label>
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
              placeholder="例: BTCUSDT-PERP"
              autoFocus
              className="rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] px-3 py-2.5 text-[12px] font-mono text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[#4C9EEB] transition-colors duration-150"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[10px] font-semibold tracking-wide text-[var(--text-muted)] uppercase font-mono">
              交易所
            </label>
            <input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="BINANCE"
              className="rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] px-3 py-2.5 text-[12px] font-mono text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[#4C9EEB] transition-colors duration-150"
            />
          </div>
          {err && <p className="text-[11px] text-[#EF5350] font-mono">{err}</p>}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-[var(--border-gray)]">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-[11px] font-bold text-[var(--text-secondary)] border border-[var(--border-gray)] hover:border-[var(--border-light)] transition-colors duration-150"
          >
            取消
          </button>
          <button
            onClick={submit}
            disabled={busy || !symbol.trim()}
            className="px-4 py-2 rounded-lg text-[11px] font-bold bg-[#26D97F] text-black disabled:opacity-50 hover:opacity-90 transition-all duration-150"
          >
            {busy ? "添加中..." : "确认添加"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Item Card ──────────────────────────────────────────────── */

function WatchCard({ item, onDelete }: { item: WatchlistItem; onDelete: (id: number) => Promise<void> }) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);


  async function handleDelete() {
    if (!confirming) { setConfirming(true); return; }
    setDeleting(true);
    try {
      await onDelete(item.id);
    } catch {
      setDeleting(false);
    }
  }

  return (
    <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 flex items-center gap-4 group hover:border-[var(--border-light)] transition-colors duration-150">
      {/* Icon */}
      <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--bg-elevated)] shrink-0">
        <Star className="w-4 h-4 text-[#f5a623]" />
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-bold font-mono text-[var(--text-primary)] truncate">
          {item.instrument_id}
        </div>
        <div className="flex items-center gap-2 mt-0.5">
          <span className="text-[10px] font-mono text-[var(--text-muted)]">
            添加于 {formatDate(item.created_at)}
          </span>
          <span className="inline-flex rounded-full bg-[#0d2e1c] px-[8px] py-[2px] text-[9px] font-bold text-[#26D97F] uppercase">
            {item.source}
          </span>
        </div>
      </div>

      {/* Delete */}
      <button
        onClick={handleDelete}
        disabled={deleting}
        onBlur={() => setConfirming(false)}
        className={`shrink-0 flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[10px] font-bold transition-all duration-150 opacity-0 group-hover:opacity-100 ${
          confirming
            ? "bg-[#EF5350] text-white"
            : "border border-[var(--border-gray)] text-[var(--text-muted)] hover:text-[#EF5350] hover:border-[#EF5350]/50"
        }`}
      >
        <Trash2 className="w-3 h-3" />
        {confirming ? "确认删除" : "删除"}
      </button>
    </div>
  );
}

/* ── Page ───────────────────────────────────────────────────── */

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);

  const fetchItems = useCallback(async () => {
    try {
      const data = await apiGet<WatchlistItem[]>("/api/watchlist");
      if (data) setItems(data);
    } catch {
      setError("加载数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchItems(); }, [fetchItems]);

  async function handleAdd(symbol: string, source: string) {
    await apiPost("/api/watchlist", { instrument_id: symbol, source });
    await fetchItems();
  }

  async function handleDelete(id: number) {
    try {
      await apiDelete(`/api/watchlist/${id}`);
      setItems((prev) => prev.filter((it) => it.id !== id));
    } catch {
      setError("删除失败");
    }
  }

  return (
    <>
      <AddDialog open={showAdd} onClose={() => setShowAdd(false)} onAdd={handleAdd} />

      <div className="flex flex-col h-full p-6 gap-5">
        {/* Header */}
        <FadeIn direction="down" duration={0.25}>
          <div className="flex items-center justify-between">
            <div className="flex flex-col gap-1">
              <h1 className="font-heading text-[26px] font-bold tracking-tight text-[var(--text-primary)]">
                自选列表
              </h1>
              <span className="text-[11px] font-mono text-[var(--text-muted)]">
                // 自定义品种监控 — 实时
              </span>
            </div>
            <button
              onClick={() => setShowAdd(true)}
              className="inline-flex items-center gap-1.5 rounded-lg bg-[#26D97F] text-black px-4 py-[9px] text-[11px] font-bold tracking-wide hover:opacity-90 transition-all duration-150"
            >
              <Plus className="w-3.5 h-3.5" />
              添加品种
            </button>
          </div>
        </FadeIn>

        {/* Content */}
        {error ? (
          <div className="flex items-center justify-center flex-1">
            <span className="font-mono text-[12px] text-[#EF5350]">{error}</span>
          </div>
        ) : loading ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 flex items-center gap-4 animate-pulse"
              >
                <div className="w-9 h-9 rounded-lg bg-[var(--border-gray)]" />
                <div className="flex-1 flex flex-col gap-2">
                  <div className="h-4 w-32 rounded bg-[var(--border-gray)]" />
                  <div className="h-3 w-20 rounded bg-[var(--border-gray)]" />
                </div>
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <FadeIn direction="up" duration={0.3} className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-4 text-center">
              <div className="flex items-center justify-center w-14 h-14 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-gray)]">
                <Star className="w-6 h-6 text-[var(--text-muted)]" />
              </div>
              <div className="flex flex-col gap-1">
                <p className="text-[13px] font-semibold text-[var(--text-secondary)]">
                  观察列表为空
                </p>
                <p className="text-[11px] text-[var(--text-muted)]">
                  点击添加按钮开始
                </p>
              </div>
              <button
                onClick={() => setShowAdd(true)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#26D97F] text-black px-4 py-[9px] text-[11px] font-bold hover:opacity-90 transition-all duration-150"
              >
                <Plus className="w-3.5 h-3.5" />
                添加第一个品种
              </button>
            </div>
          </FadeIn>
        ) : (
          <StaggerContainer className="flex flex-col gap-2" staggerDelay={0.05}>
            {items.map((item) => (
              <StaggerItem key={item.id}>
                <WatchCard item={item} onDelete={handleDelete} />
              </StaggerItem>
            ))}
          </StaggerContainer>
        )}
      </div>
    </>
  );
}
