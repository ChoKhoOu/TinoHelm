"use client";

import { useState, useEffect, useMemo } from "react";
import { Search, RefreshCw, CheckCircle, ChevronRight, FileText, Layers } from "lucide-react";
import { apiGet, apiPost } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeIn } from "@/components/motion/FadeIn";

/* ── Types ───────────────────────────────────────────────────────── */

interface RawStrategyItem {
  name: string;
  strategy_class?: string;
  file_path?: string;
  type?: string;
  version?: string;
  description?: string;
}

interface StrategyListItem {
  name: string;
  class_name?: string;
  file_path?: string;
  is_portfolio?: boolean;
  version?: string;
  description?: string;
}

interface StrategyParam {
  name: string;
  type: string;
  default?: unknown;
  min?: number;
  max?: number;
  description?: string;
}

interface StrategyVersion {
  version: string;
  date?: string;
  notes?: string;
}

interface StrategyDetail {
  name: string;
  class_name?: string;
  file_path?: string;
  is_portfolio?: boolean;
  version?: string;
  description?: string;
  versions?: StrategyVersion[];
}

/* ── Helpers ─────────────────────────────────────────────────────── */

function formatPath(path: string | undefined): string {
  if (!path) return "—";
  const parts = path.split(/[/\\]/);
  return parts.slice(-2).join("/");
}

/* ── Skeleton loaders ────────────────────────────────────────────── */

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-2 p-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="rounded-lg p-3 flex flex-col gap-2">
          <Skeleton className="h-3.5 w-2/3 bg-[var(--bg-elevated)]" />
          <Skeleton className="h-2.5 w-1/2 bg-[var(--bg-elevated)]" />
        </div>
      ))}
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-5 p-6">
      <div className="flex flex-col gap-2">
        <Skeleton className="h-6 w-48 bg-[var(--bg-elevated)]" />
        <Skeleton className="h-3 w-32 bg-[var(--bg-elevated)]" />
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-3 w-20 bg-[var(--bg-elevated)]" />
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full bg-[var(--bg-elevated)]" />
        ))}
      </div>
    </div>
  );
}

/* ── Strategy list item ──────────────────────────────────────────── */

function StrategyRow({
  strategy,
  selected,
  onClick,
}: {
  strategy: StrategyListItem;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-lg px-3 py-2.5 flex items-center gap-3 transition-all duration-100 group ${
        selected
          ? "bg-[var(--accent-green-20)] border border-[var(--accent-green)]/30"
          : "hover:bg-[var(--bg-elevated)] border border-transparent"
      }`}
    >
      <div className="flex-1 min-w-0 flex flex-col gap-0.5">
        <div className="flex items-center gap-2">
          <span
            className={`text-[12px] font-semibold truncate ${
              selected ? "text-[var(--accent-green)]" : "text-[var(--text-primary)]"
            }`}
          >
            {strategy.name}
          </span>
          <span
            className={`shrink-0 inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold ${
              strategy.is_portfolio
                ? "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]"
                : "bg-[var(--bg-elevated)] text-[var(--text-muted)]"
            }`}
          >
            {strategy.is_portfolio ? "组合" : "单策略"}
          </span>
        </div>
        {strategy.class_name && (
          <span className="text-[10px] text-[var(--text-muted)] truncate font-mono">
            {strategy.class_name}
          </span>
        )}
      </div>
      {strategy.version && (
        <span className="shrink-0 text-[9px] font-mono text-[var(--text-muted)]">
          v{strategy.version}
        </span>
      )}
      <ChevronRight
        className={`shrink-0 w-3 h-3 transition-colors ${
          selected ? "text-[var(--accent-green)]" : "text-[var(--text-muted)] opacity-0 group-hover:opacity-100"
        }`}
      />
    </button>
  );
}

/* ── Detail panel ────────────────────────────────────────────────── */

function DetailPanel({
  strategy,
  detail,
  params,
  detailLoading,
  paramsLoading,
  onValidate,
  validating,
  validateResult,
}: {
  strategy: StrategyListItem;
  detail: StrategyDetail | null;
  params: StrategyParam[];
  detailLoading: boolean;
  paramsLoading: boolean;
  onValidate: () => void;
  validating: boolean;
  validateResult: string | null;
}) {
  if (detailLoading) return <DetailSkeleton />;

  const info = detail ?? strategy;

  return (
    <FadeIn key={strategy.name} className="flex flex-col gap-5 p-6 overflow-y-auto h-full">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <h2 className="font-heading text-xl font-bold text-[var(--text-primary)]">
              {info.name}
            </h2>
            <span
              className={`inline-flex rounded-full px-2.5 py-1 text-[9px] font-bold ${
                info.is_portfolio
                  ? "bg-[var(--accent-blue-20)] text-[var(--accent-blue)]"
                  : "bg-[var(--bg-elevated)] text-[var(--text-muted)]"
              }`}
            >
              {info.is_portfolio ? "组合策略" : "单策略"}
            </span>
          </div>
          {info.description && (
            <span className="text-[11px] text-[var(--text-secondary)]">{info.description}</span>
          )}
        </div>
        <button
          onClick={onValidate}
          disabled={validating}
          className="shrink-0 inline-flex items-center gap-1.5 rounded-lg border border-[var(--border-gray)] bg-[var(--bg-elevated)] px-3 py-1.5 text-[11px] font-semibold text-[var(--text-secondary)] hover:border-[var(--accent-green)]/50 hover:text-[var(--accent-green)] transition-all duration-150 disabled:opacity-50"
        >
          {validating ? (
            <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
          ) : (
            <CheckCircle className="w-3 h-3" />
          )}
          验证
        </button>
      </div>

      {validateResult && (
        <div
          className={`rounded-lg px-4 py-2.5 text-[11px] font-medium border ${
            validateResult === "ok"
              ? "bg-[var(--accent-green-20)] border-[var(--accent-green)]/30 text-[var(--accent-green)]"
              : "bg-[var(--accent-red-20)] border-[var(--accent-red)]/30 text-[var(--accent-red)]"
          }`}
        >
          {validateResult === "ok" ? "验证通过" : `验证失败: ${validateResult}`}
        </div>
      )}

      {/* 概览 */}
      <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] p-5 flex flex-col gap-4">
        <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
          概览
        </span>
        <div className="grid grid-cols-2 gap-x-8 gap-y-3">
          <KvRow label="策略名称" value={info.name} />
          <KvRow label="策略类" value={info.class_name ?? "—"} mono />
          <KvRow label="类型" value={info.is_portfolio ? "组合策略" : "单策略"} />
          <KvRow label="版本" value={info.version ? `v${info.version}` : "—"} />
          <div className="col-span-2">
            <KvRow label="文件路径" value={info.file_path ?? "—"} mono />
          </div>
        </div>
      </div>

      {/* 参数 */}
      <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden">
        <div className="px-5 py-3 border-b border-[var(--border-gray)] flex items-center justify-between">
          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
            参数
          </span>
          {paramsLoading && (
            <div className="w-3 h-3 border border-[var(--text-muted)] border-t-transparent rounded-full animate-spin" />
          )}
        </div>
        {paramsLoading ? (
          <div className="p-5 flex flex-col gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full bg-[var(--bg-elevated)]" />
            ))}
          </div>
        ) : params.length === 0 ? (
          <div className="px-5 py-6 text-center text-[11px] text-[var(--text-muted)]">
            暂无参数配置
          </div>
        ) : (
          <div>
            {/* Table header */}
            <div className="flex items-center px-5 py-2 border-b border-[var(--border-gray)]">
              <span className="w-[160px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">参数名</span>
              <span className="w-[80px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">类型</span>
              <span className="w-[120px] text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">默认值</span>
              <span className="flex-1 text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">优化范围</span>
            </div>
            {params.map((p, i) => (
              <div
                key={p.name}
                className={`flex items-center px-5 py-2.5 text-[11px] ${
                  i < params.length - 1 ? "border-b border-[var(--border-gray)]" : ""
                }`}
              >
                <span className="w-[160px] font-mono text-[var(--text-primary)] truncate">{p.name}</span>
                <span className="w-[80px]">
                  <span className="inline-flex rounded-full px-2 py-0.5 text-[9px] font-bold bg-[var(--accent-blue-20)] text-[var(--accent-blue)]">
                    {p.type}
                  </span>
                </span>
                <span className="w-[120px] font-mono text-[var(--text-secondary)]">
                  {p.default !== undefined && p.default !== null ? String(p.default) : "—"}
                </span>
                <span className="flex-1 font-mono text-[var(--text-muted)] text-[10px]">
                  {p.min !== undefined && p.max !== undefined
                    ? `[${p.min}, ${p.max}]`
                    : "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 版本历史 */}
      {detail?.versions && detail.versions.length > 0 && (
        <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-gray)] overflow-hidden">
          <div className="px-5 py-3 border-b border-[var(--border-gray)]">
            <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
              版本历史
            </span>
          </div>
          <div className="flex flex-col">
            {detail.versions.map((v, i) => (
              <div
                key={v.version}
                className={`flex items-center gap-4 px-5 py-2.5 text-[11px] ${
                  i < detail.versions!.length - 1 ? "border-b border-[var(--border-gray)]" : ""
                }`}
              >
                <span className="font-mono text-[var(--accent-green)] w-16">v{v.version}</span>
                {v.date && (
                  <span className="text-[var(--text-muted)] w-28">{v.date}</span>
                )}
                {v.notes && (
                  <span className="text-[var(--text-secondary)] flex-1">{v.notes}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </FadeIn>
  );
}

function KvRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
        {label}
      </span>
      <span className={`text-[11px] text-[var(--text-primary)] ${mono ? "font-mono" : "font-medium"}`}>
        {value}
      </span>
    </div>
  );
}

/* ── Page ────────────────────────────────────────────────────────── */

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<StrategyListItem | null>(null);

  const [detail, setDetail] = useState<StrategyDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const [params, setParams] = useState<StrategyParam[]>([]);
  const [paramsLoading, setParamsLoading] = useState(false);

  const [rescanning, setRescanning] = useState(false);
  const [validating, setValidating] = useState(false);
  const [validateResult, setValidateResult] = useState<string | null>(null);

  useEffect(() => {
    loadStrategies();
  }, []);

  async function loadStrategies() {
    setLoading(true);
    setError(null);
    try {
      const raw = await apiGet<RawStrategyItem[]>("/api/strategies");
      if (raw) setStrategies(raw.map((s) => ({
        name: s.name,
        class_name: s.strategy_class,
        file_path: s.file_path,
        is_portfolio: s.type === "portfolio",
        version: s.version,
        description: s.description,
      })));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleRescan() {
    setRescanning(true);
    try {
      await apiPost("/api/strategies/rescan");
      await loadStrategies();
    } catch {
      // ignore
    } finally {
      setRescanning(false);
    }
  }

  async function handleSelect(s: StrategyListItem) {
    setSelected(s);
    setDetail(null);
    setParams([]);
    setValidateResult(null);

    setDetailLoading(true);
    setParamsLoading(true);

    try {
      const d = await apiGet<StrategyDetail>(`/api/strategies/${encodeURIComponent(s.name)}`);
      if (d) setDetail(d);
    } catch {
      // ignore
    } finally {
      setDetailLoading(false);
    }

    try {
      const p = await apiGet<StrategyParam[]>(
        `/api/strategies/${encodeURIComponent(s.name)}/params`
      );
      if (p) setParams(p);
    } catch {
      // ignore
    } finally {
      setParamsLoading(false);
    }
  }

  async function handleValidate() {
    if (!selected) return;
    setValidating(true);
    setValidateResult(null);
    try {
      await apiPost(`/api/strategies/${encodeURIComponent(selected.name)}/validate`);
      setValidateResult("ok");
    } catch (err) {
      setValidateResult(err instanceof Error ? err.message : "error");
    } finally {
      setValidating(false);
    }
  }

  const filtered = useMemo(() => {
    if (!search.trim()) return strategies;
    const q = search.toLowerCase();
    return strategies.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.class_name && s.class_name.toLowerCase().includes(q))
    );
  }, [strategies, search]);

  return (
    <div className="flex flex-col h-full">
      {/* Top bar */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-gray)] shrink-0">
        <div className="flex flex-col gap-0.5">
          <h1 className="font-heading text-[22px] font-bold tracking-tight text-[var(--text-primary)]">
            策略管理
          </h1>
          <span className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">
            // {loading ? "加载中..." : `${strategies.length} 个策略已发现`}
          </span>
        </div>
        <button
          onClick={handleRescan}
          disabled={rescanning}
          className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent-green)] text-[var(--text-on-accent)] px-4 py-2 text-[11px] font-bold tracking-wide hover:opacity-90 transition-all duration-150 disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${rescanning ? "animate-spin" : ""}`} />
          重新扫描
        </button>
      </div>

      {/* Body: master-detail */}
      <div className="flex flex-1 min-h-0">
        {/* Left panel — 380px */}
        <div className="w-[380px] shrink-0 flex flex-col border-r border-[var(--border-gray)]">
          {/* Search */}
          <div className="px-3 py-3 border-b border-[var(--border-gray)]">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-muted)]" />
              <input
                type="text"
                placeholder="搜索策略..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full h-8 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border-gray)] pl-8 pr-3 text-[11px] text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none focus:border-[var(--accent-green)]/50 transition-colors"
              />
            </div>
          </div>

          {/* List */}
          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <ListSkeleton />
            ) : error ? (
              <div className="flex flex-col items-center justify-center h-full gap-2 p-6">
                <span className="text-[11px] text-[var(--accent-red)]">{error}</span>
                <button
                  onClick={loadStrategies}
                  className="text-[10px] text-[var(--text-muted)] underline"
                >
                  重试
                </button>
              </div>
            ) : filtered.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full gap-2 p-6">
                <span className="text-[11px] text-[var(--text-muted)]">
                  {search ? "无匹配结果" : "暂无策略"}
                </span>
              </div>
            ) : (
              <div className="flex flex-col gap-1 p-3">
                {filtered.map((s) => (
                  <StrategyRow
                    key={s.name}
                    strategy={s}
                    selected={selected?.name === s.name}
                    onClick={() => handleSelect(s)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Right panel — flex-1 */}
        <div className="flex-1 min-w-0 overflow-hidden">
          {selected ? (
            <DetailPanel
              strategy={selected}
              detail={detail}
              params={params}
              detailLoading={detailLoading}
              paramsLoading={paramsLoading}
              onValidate={handleValidate}
              validating={validating}
              validateResult={validateResult}
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-gray)]">
                {strategies.length > 0 ? (
                  <Layers className="w-5 h-5 text-[var(--text-muted)]" />
                ) : (
                  <FileText className="w-5 h-5 text-[var(--text-muted)]" />
                )}
              </div>
              <span className="text-[11px] text-[var(--text-muted)]">请选择一个策略</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
