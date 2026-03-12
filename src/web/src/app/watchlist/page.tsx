"use client";

import { Plus, X } from "lucide-react";
import { useState, useEffect, useCallback } from "react";
import { apiGet, apiPost, apiDelete } from "@/lib/api";
import { useI18n } from "@/i18n";

/* ── Types ──────────────────────────────────────────────────── */

interface WatchlistItem {
  id: number;
  instrument_id: string;
  source: string;
  created_at: string | null;
}

/* ── Page ───────────────────────────────────────────────────── */

export default function WatchlistPage() {
  const { t } = useI18n();
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [newInstrument, setNewInstrument] = useState("");
  const [newSource, setNewSource] = useState("BINANCE");

  const fetchItems = useCallback(async () => {
    try {
      const data = await apiGet<WatchlistItem[]>("/api/watchlist");
      if (data) setItems(data);
    } catch {
      setError("watchlist.loadFailed");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  async function handleAdd() {
    if (!newInstrument.trim()) return;
    try {
      await apiPost("/api/watchlist", {
        instrument_id: newInstrument.trim(),
        source: newSource.trim() || "BINANCE",
      });
      setNewInstrument("");
      setNewSource("BINANCE");
      setShowAddForm(false);
      await fetchItems();
    } catch {
      setError("watchlist.addFailed");
    }
  }

  async function handleDelete(id: number) {
    try {
      await apiDelete(`/api/watchlist/${id}`);
      setItems((prev) => prev.filter((item) => item.id !== id));
    } catch {
      setError("watchlist.removeFailed");
    }
  }

  return (
    <div className="flex flex-col gap-6 p-8">
      {/* Top bar */}
      <div className="flex items-end justify-between">
        <div className="flex flex-col gap-1">
          <h1 className="font-heading text-[28px] font-bold tracking-tight text-[var(--text-primary)]">
            {t("watchlist.title")}
          </h1>
          <span className="text-[11px] font-medium text-[var(--text-muted)]">
            {t("watchlist.subtitle")}
          </span>
        </div>
        <button
          onClick={() => setShowAddForm((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-5 py-[10px] text-[11px] font-bold tracking-wide hover:opacity-90 transition-all duration-150"
        >
          <Plus className="w-3 h-3" />
          {t("watchlist.addInstrument")}
        </button>
      </div>

      {/* Add form */}
      {showAddForm && (
        <div className="flex items-center gap-3 p-4 rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)]">
          <input
            value={newInstrument}
            onChange={(e) => setNewInstrument(e.target.value)}
            placeholder="BTCUSDT-PERP"
            className="flex-1 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] px-3 py-2 text-[11px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent-green)] transition-colors duration-150"
          />
          <input
            value={newSource}
            onChange={(e) => setNewSource(e.target.value)}
            placeholder="BINANCE"
            className="w-[120px] rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] px-3 py-2 text-[11px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent-green)] transition-colors duration-150"
          />
          <button
            onClick={handleAdd}
            className="rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-4 py-2 text-[11px] font-bold tracking-wide hover:opacity-90 transition-all duration-150"
          >
            {t("watchlist.add")}
          </button>
        </div>
      )}

      {/* Cards grid */}
      {error ? (
        <div className="flex items-center justify-center h-full p-8">
          <span className="font-mono text-[12px] text-[var(--accent-red)]">{t(error as "watchlist.loadFailed")}</span>
        </div>
      ) : loading ? (
        <div className="text-center py-12 text-[11px] text-[var(--text-muted)]">
          {t("watchlist.loading")}
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-[11px] text-[var(--text-muted)]">
          {t("watchlist.noItems")}
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-4">
          {items.map((item) => (
            <div
              key={item.id}
              className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 relative group"
            >
              {/* Delete button */}
              <button
                onClick={() => handleDelete(item.id)}
                aria-label="Remove from watchlist"
                className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 rounded-md p-1 text-[var(--text-muted)] hover:text-[var(--accent-red)] hover:bg-[var(--accent-red-20)] transition-all duration-150"
              >
                <X className="w-3 h-3" />
              </button>
              {/* Top row */}
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-medium text-[var(--text-secondary)]">
                  {item.instrument_id}
                </span>
                <span className="inline-flex rounded-full bg-[var(--accent-green-20)] px-[8px] py-[2px] text-[9px] font-bold text-[var(--accent-green)]">
                  {item.source}
                </span>
              </div>
              {/* Price placeholder */}
              <div className="font-heading text-2xl font-bold mt-3 text-[var(--text-secondary)]">
                —
              </div>
              {/* Change placeholder */}
              <div className="text-[11px] font-medium mt-1 text-[var(--text-muted)]">
                — (—)
              </div>
              {/* Flat sparkline placeholder */}
              <svg
                width="120"
                height="32"
                viewBox="0 0 120 32"
                fill="none"
                className="mt-2"
              >
                <path
                  d="M0 16 L120 16"
                  stroke="var(--border-gray)"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeDasharray="4 4"
                />
              </svg>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
