"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Plus, Trash2, Star, Bell } from "lucide-react";
import { apiGet, apiPost, apiDelete } from "@/lib/api";
import { FadeIn } from "@/components/motion/FadeIn";
import { EmptyState } from "@/components/EmptyState";
import { TickFlash } from "@/components/TickFlash";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

/* ── Types ──────────────────────────────────────────────── */

interface WatchlistItem {
  id: number;
  instrument_id: string;
  source: string;
  created_at: string | null;
}

interface WatchlistGroup {
  name: string;
  items: WatchlistItem[];
}

interface QuoteRow {
  id: number;
  symbol: string;
  last: number;
  chg: number;
  chgPct: number;
  high: number;
  low: number;
  volume: number;
  funding: number;
  spread: number;
  alert: string | null;
  sparkData: number[];
}

/* ── Mock ────────────────────────────────────────────────── */

function mockWatchlists(): WatchlistGroup[] {
  return [
    { name: "Default", items: [] },
    { name: "BTC Hedges", items: [] },
    { name: "High Vol", items: [] },
  ];
}

function mockQuotes(): QuoteRow[] {
  const data: { sym: string; last: number; chg: number; vol: number; fund: number; spread: number; alert: string | null }[] = [
    { sym: "BTC-PERP", last: 67120, chg: 1240, vol: 2.4e9, fund: 0.0082, spread: 0.8, alert: null },
    { sym: "ETH-PERP", last: 3445, chg: -52, vol: 1.1e9, fund: 0.0045, spread: 1.2, alert: null },
    { sym: "SOL-PERP", last: 171.2, chg: 4.8, vol: 380e6, fund: 0.012, spread: 2.1, alert: null },
    { sym: "ARB-PERP", last: 1.102, chg: -0.024, vol: 85e6, fund: -0.0032, spread: 4.5, alert: null },
    { sym: "DOGE-PERP", last: 0.168, chg: 0.0045, vol: 220e6, fund: 0.0018, spread: 3.2, alert: "> 0.175" },
    { sym: "OP-PERP", last: 2.34, chg: -0.12, vol: 140e6, fund: -0.0015, spread: 3.8, alert: null },
    { sym: "AVAX-PERP", last: 38.5, chg: 1.2, vol: 195e6, fund: 0.0028, spread: 2.4, alert: null },
    { sym: "LINK-PERP", last: 14.8, chg: -0.35, vol: 165e6, fund: 0.0012, spread: 2.9, alert: "< 14.0" },
  ];

  return data.map((d, i) => ({
    id: i,
    symbol: d.sym,
    last: d.last,
    chg: d.chg,
    chgPct: (d.chg / d.last) * 100,
    high: d.last + Math.abs(d.chg) * 0.8,
    low: d.last - Math.abs(d.chg) * 0.6,
    volume: d.vol,
    funding: d.fund,
    spread: d.spread,
    alert: d.alert,
    sparkData: Array.from({ length: 20 }, () => Math.random() * 16 + 2),
  }));
}

/* ── Sparkline Canvas ────────────────────────────────────── */

function Sparkline({ data, positive }: { data: number[]; positive: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const ctx = ref.current?.getContext("2d");
    if (!ctx || data.length === 0) return;
    const w = 60, h = 20;
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    data.forEach((p, j) => {
      const x = (j / (data.length - 1)) * w;
      j === 0 ? ctx.moveTo(x, p) : ctx.lineTo(x, p);
    });
    ctx.strokeStyle = positive ? "#36884B" : "#FE8181";
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }, [data, positive]);

  return <canvas ref={ref} width={60} height={20} className="inline-block align-middle" />;
}

/* ── Add Symbol Dialog ───────────────────────────────────── */

function AddSymbolDialog({
  open,
  onClose,
  onAdd,
}: {
  open: boolean;
  onClose: () => void;
  onAdd: (symbol: string, source: string) => void;
}) {
  const [symbol, setSymbol] = useState("");
  const [source, setSource] = useState("BINANCE");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!open) { setSymbol(""); setSource("BINANCE"); setErr(""); }
  }, [open]);

  function submit() {
    if (!symbol.trim()) { setErr("Please enter a symbol"); return; }
    onAdd(symbol.trim(), source.trim() || "BINANCE");
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent showCloseButton={false} className="max-w-[400px]">
        <DialogHeader>
          <DialogTitle>Add Symbol</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div>
            <label className="text-[0.72rem] font-mono text-muted-foreground mb-1 block">Symbol</label>
            <Input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
              placeholder="e.g. BTCUSDT-PERP"
              autoFocus
              className="font-mono text-[0.78rem]"
            />
          </div>
          <div>
            <label className="text-[0.72rem] font-mono text-muted-foreground mb-1 block">Exchange</label>
            <Input
              value={source}
              onChange={(e) => setSource(e.target.value)}
              placeholder="BINANCE"
              className="font-mono text-[0.78rem]"
            />
          </div>
          {err && <p className="text-[0.72rem] text-destructive font-mono">{err}</p>}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} disabled={!symbol.trim()}>Add</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Alert Dialog ────────────────────────────────────────── */

function AlertDialog({
  open,
  symbol,
  currentAlert,
  onClose,
  onSave,
}: {
  open: boolean;
  symbol: string;
  currentAlert: string | null;
  onClose: () => void;
  onSave: (alert: string | null) => void;
}) {
  const [value, setValue] = useState(currentAlert ?? "");

  useEffect(() => {
    if (open) setValue(currentAlert ?? "");
  }, [open, currentAlert]);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent showCloseButton={false} className="max-w-[360px]">
        <DialogHeader>
          <DialogTitle>Price Alert — {symbol}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <label className="text-[0.72rem] font-mono text-muted-foreground">
            Alert condition (e.g. &gt; 70000 or &lt; 3000)
          </label>
          <Input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { onSave(value.trim() || null); onClose(); } }}
            placeholder="> 70000"
            autoFocus
            className="font-mono text-[0.78rem]"
          />
        </div>
        <DialogFooter>
          {currentAlert && (
            <Button variant="ghost" onClick={() => { onSave(null); onClose(); }} className="mr-auto text-destructive">
              Remove
            </Button>
          )}
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={() => { onSave(value.trim() || null); onClose(); }}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/* ── Helpers ─────────────────────────────────────────────── */

function fmtPrice(v: number): string {
  if (v < 1) return v.toFixed(4);
  if (v < 10) return v.toFixed(3);
  return v.toFixed(2);
}

function fmtVol(v: number): string {
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${(v / 1e3).toFixed(0)}K`;
}

function spreadColor(bps: number): string {
  if (bps < 1) return "var(--suc)";
  if (bps <= 5) return "var(--t0)";
  return "var(--acc)";
}

function fundingColor(rate: number): string {
  if (rate > 0) return "var(--suc)";
  if (rate < 0) return "var(--dan)";
  return "var(--t2)";
}

/* ── Page ────────────────────────────────────────────────── */

export default function WatchlistPage() {
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [quotes, setQuotes] = useState<QuoteRow[]>(mockQuotes);
  const [activeList, setActiveList] = useState("Default");
  const [lists] = useState(() => {
    if (typeof window !== "undefined") {
      const saved = localStorage.getItem("tino:watchlists");
      if (saved) {
        try { return JSON.parse(saved) as string[]; } catch { /* ignore */ }
      }
    }
    return ["Default", "BTC Hedges", "High Vol"];
  });
  const [alertDialog, setAlertDialog] = useState<{ open: boolean; idx: number }>({ open: false, idx: 0 });

  const fetchItems = useCallback(async () => {
    try {
      const data = await apiGet<WatchlistItem[]>("/api/watchlist");
      if (data) setItems(data);
    } catch {
      // Use mock data silently
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchItems(); }, [fetchItems]);

  /* Simulate real-time price ticks */
  useEffect(() => {
    const interval = setInterval(() => {
      setQuotes((prev) =>
        prev.map((q) => {
          const delta = q.last * (Math.random() - 0.5) * 0.0005;
          const newLast = q.last + delta;
          return {
            ...q,
            last: newLast,
            chg: q.chg + delta,
            chgPct: ((q.chg + delta) / newLast) * 100,
          };
        }),
      );
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  async function handleAdd(symbol: string, source: string) {
    try {
      await apiPost("/api/watchlist", { instrument_id: symbol, source });
      await fetchItems();
    } catch {
      // silently handle
    }
  }

  async function handleDelete(id: number) {
    try {
      await apiDelete(`/api/watchlist/${id}`);
      setItems((prev) => prev.filter((it) => it.id !== id));
    } catch {
      // silently handle
    }
  }

  function handleAlertSave(alert: string | null) {
    setQuotes((prev) =>
      prev.map((q, i) => (i === alertDialog.idx ? { ...q, alert } : q)),
    );
  }

  const hasQuotes = quotes.length > 0;

  return (
    <>
      <AddSymbolDialog open={showAdd} onClose={() => setShowAdd(false)} onAdd={handleAdd} />
      <AlertDialog
        open={alertDialog.open}
        symbol={quotes[alertDialog.idx]?.symbol ?? ""}
        currentAlert={quotes[alertDialog.idx]?.alert ?? null}
        onClose={() => setAlertDialog({ open: false, idx: 0 })}
        onSave={handleAlertSave}
      />

      <div className="flex flex-col h-full p-6 gap-5 overflow-y-auto">
        {/* Header */}
        <FadeIn direction="down" duration={0.25}>
          <div className="flex items-center justify-between">
            <div className="flex flex-col gap-1">
              <h1 className="font-heading text-[1.3rem] font-bold tracking-tight text-foreground">
                Watchlist
              </h1>
              <span className="text-[0.68rem] font-mono text-muted-foreground">
                Custom symbol monitoring — real-time
              </span>
            </div>
            <Button
              onClick={() => setShowAdd(true)}
              variant="ghost"
              className="text-[0.72rem] font-mono border hover:border-qds-border-hover hover:bg-secondary"
            >
              <Plus className="w-3.5 h-3.5 mr-1.5" />
              Add Symbol
            </Button>
          </div>
        </FadeIn>

        {/* Watchlist tabs */}
        <FadeIn direction="up" duration={0.25} delay={0.05}>
          <div className="flex items-center gap-[2px] rounded-sm bg-input p-[3px] w-fit">
            {lists.map((name) => (
              <button
                key={name}
                onClick={() => setActiveList(name)}
                className={`rounded px-3 py-1.5 text-[0.72rem] font-mono font-medium transition-all duration-150 ${
                  activeList === name
                    ? "bg-secondary text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-qds-t1"
                }`}
              >
                {name}
              </button>
            ))}
            <button className="rounded px-2 py-1.5 text-[0.72rem] font-mono text-primary hover:bg-secondary transition-all duration-150">
              +
            </button>
          </div>
        </FadeIn>

        {/* Quote table */}
        <FadeIn direction="up" duration={0.25} delay={0.1} className="flex-1 min-h-0">
          {!hasQuotes ? (
            <EmptyState
              variant="first-use"
              icon={<Star className="size-6 text-muted-foreground" />}
              title="Watchlist is empty"
              description="Add symbols to start monitoring prices"
              action={{ label: "Add Symbol", onClick: () => setShowAdd(true) }}
            />
          ) : (
            <div className="rounded-xl bg-card border overflow-hidden">
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Symbol</TableHead>
                      <TableHead className="text-right">Last</TableHead>
                      <TableHead className="text-right">24h Chg</TableHead>
                      <TableHead className="text-right">24h Chg%</TableHead>
                      <TableHead className="text-right">High</TableHead>
                      <TableHead className="text-right">Low</TableHead>
                      <TableHead className="text-right">Volume</TableHead>
                      <TableHead className="text-right">Funding</TableHead>
                      <TableHead className="text-right">Spread</TableHead>
                      <TableHead className="text-center">7d</TableHead>
                      <TableHead className="text-center">Alert</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {quotes.map((q, idx) => (
                      <TableRow key={q.symbol}>
                        <TableCell className="font-semibold">{q.symbol}</TableCell>
                        <TableCell className="text-right">
                          <TickFlash value={q.last}>
                            <span>{fmtPrice(q.last)}</span>
                          </TickFlash>
                        </TableCell>
                        <TableCell className="text-right" style={{ color: q.chg >= 0 ? "var(--suc)" : "var(--dan)" }}>
                          {q.chg >= 0 ? "+" : ""}{fmtPrice(q.chg)}
                        </TableCell>
                        <TableCell className="text-right" style={{ color: q.chgPct >= 0 ? "var(--suc)" : "var(--dan)" }}>
                          {q.chgPct >= 0 ? "+" : ""}{q.chgPct.toFixed(2)}%
                        </TableCell>
                        <TableCell className="text-right">{fmtPrice(q.high)}</TableCell>
                        <TableCell className="text-right">{fmtPrice(q.low)}</TableCell>
                        <TableCell className="text-right">{fmtVol(q.volume)}</TableCell>
                        <TableCell className="text-right" style={{ color: fundingColor(q.funding) }}>
                          {(q.funding * 100).toFixed(3)}%
                        </TableCell>
                        <TableCell className="text-right" style={{ color: spreadColor(q.spread) }}>
                          {q.spread.toFixed(1)}bp
                        </TableCell>
                        <TableCell className="text-center">
                          <Sparkline data={q.sparkData} positive={q.chg >= 0} />
                        </TableCell>
                        <TableCell className="text-center">
                          <button
                            onClick={() => setAlertDialog({ open: true, idx })}
                            className={`rounded-full px-2 py-0.5 text-[0.62rem] font-mono border transition-all duration-150 ${
                              q.alert
                                ? "bg-qds-warning-dim text-qds-warning border-qds-warning"
                                : "border-dashed border text-qds-t3 hover:border-primary hover:text-primary"
                            }`}
                          >
                            {q.alert ?? "Set"}
                          </button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
          )}
        </FadeIn>
      </div>
    </>
  );
}
