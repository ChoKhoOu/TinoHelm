"use client";

import { useState, useEffect, useMemo } from "react";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { Search, RefreshCw, Plus } from "lucide-react";
import { apiGet, apiPost, ApiError } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { FadeIn } from "@/components/motion/FadeIn";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/* -- Types ---------------------------------------------------------- */

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
  version?: string;
  description?: string;
}

interface StrategyParam {
  name: string;
  type: string;
  default?: unknown;
  min?: number;
  max?: number;
}

interface ParamsResponse {
  name: string;
  config_params: Array<{ name: string; type: string; required: boolean; default: string | null }>;
  optimize_ranges: Record<string, { type: string; min: number; max: number; step?: number }>;
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
  version?: string;
  description?: string;
  versions?: StrategyVersion[];
}

/* -- Skeleton loaders ----------------------------------------------- */

function ListSkeleton() {
  return (
    <div className="flex flex-col">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="grid border-b last:border-0 py-3 px-4 gap-3 items-center"
          style={{ gridTemplateColumns: "3px 1fr auto auto auto" }}
        >
          <div />
          <div className="flex flex-col gap-1.5">
            <Skeleton className="h-3 w-2/5 bg-secondary" />
            <Skeleton className="h-2.5 w-3/5 bg-secondary" />
          </div>
          <Skeleton className="h-2.5 w-8 bg-secondary" />
          <Skeleton className="h-2.5 w-10 bg-secondary" />
          <div />
        </div>
      ))}
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-4 pb-5 border-b">
        <Skeleton className="h-8 w-16 bg-secondary rounded-sm" />
        <Skeleton className="size-10 rounded-[10px] bg-secondary" />
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-4 w-40 bg-secondary" />
          <Skeleton className="h-3 w-60 bg-secondary" />
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-3 w-16 bg-secondary" />
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full bg-secondary rounded-sm" />
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-3 w-20 bg-secondary" />
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-9 w-full bg-secondary" />
        ))}
      </div>
    </div>
  );
}

/* -- Sub-components ------------------------------------------------- */

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="qds-section-label mb-3">
      {children}
    </div>
  );
}

function TypeBadge({ type }: { type: string }) {
  const t = type.toLowerCase();
  let cls = "bg-qds-info-dim text-qds-info";
  if (t === "bool" || t === "boolean") cls = "bg-qds-warning-dim text-qds-warning";
  else if (t === "int" || t === "integer") cls = "bg-qds-success-dim text-qds-success";
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[0.58rem] font-bold ${cls}`}>
      [{type}]
    </span>
  );
}

/* -- Strategy row (list view) --------------------------------------- */

function StrategyRow({ strategy, onClick }: { strategy: StrategyListItem; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      className="grid items-center border-b last:border-0 cursor-pointer hover:bg-secondary transition-colors group"
      style={{ gridTemplateColumns: "3px 1fr auto auto auto" }}
    >
      <div className="self-stretch rounded-l-sm bg-primary" />
      <div className="py-3 px-4">
        <div className="flex items-center gap-2">
          <span className="text-[0.82rem] font-semibold font-mono text-foreground">{strategy.name}</span>
          <span className="px-1.5 py-0.5 rounded-full text-[0.58rem] font-bold bg-secondary text-qds-t1">策略</span>
        </div>
        <div className="text-[0.68rem] font-mono text-muted-foreground mt-0.5">
          {strategy.class_name && strategy.file_path
            ? `${strategy.class_name} · ${strategy.file_path}`
            : strategy.class_name ?? strategy.file_path ?? ""}
        </div>
      </div>
      <div className="py-3 px-3 text-[0.72rem] font-mono text-muted-foreground">
        {strategy.version ? `v${strategy.version}` : ""}
      </div>
      <div className="py-3 px-3 text-[0.72rem] font-mono text-muted-foreground" />
      <div className="py-3 px-3 text-[0.72rem] text-qds-t3 group-hover:text-primary group-hover:translate-x-[3px] transition-all">
        →
      </div>
    </div>
  );
}

/* -- Detail view ---------------------------------------------------- */

function DetailView({
  strategy,
  detail,
  params,
  detailLoading,
  paramsLoading,
  validating,
  validateResult,
  onBack,
  onValidate,
  onOpen,
}: {
  strategy: StrategyListItem;
  detail: StrategyDetail | null;
  params: StrategyParam[];
  detailLoading: boolean;
  paramsLoading: boolean;
  validating: boolean;
  validateResult: string | null;
  onBack: () => void;
  onValidate: () => void;
  onOpen: () => void;
}) {
  const info = detail ?? strategy;
  const name = info.name;
  const iconLetters = name.substring(0, 2).toUpperCase();

  if (detailLoading) return <DetailSkeleton />;

  return (
    <div className="flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center gap-4 pb-5 border-b flex-wrap">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-sm border text-[0.75rem] font-mono text-qds-t1 hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all group shrink-0"
        >
          <span className="group-hover:-translate-x-[3px] transition-transform inline-block">←</span>
          &nbsp;返回
        </button>
        <div
          className="size-10 rounded-[10px] flex items-center justify-center text-[0.82rem] font-mono font-semibold shrink-0 bg-qds-accent-dim text-primary"
        >
          {iconLetters}
        </div>
        <div className="flex flex-col gap-0.5 min-w-0">
          <div className="text-[1.05rem] font-mono font-semibold text-foreground">{name}</div>
          <div className="text-[0.68rem] font-mono text-muted-foreground truncate">{info.file_path ?? ""}</div>
        </div>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <span className="px-1.5 py-0.5 rounded-full text-[0.58rem] font-bold bg-secondary text-qds-t1">
            策略
          </span>
          <button
            onClick={onOpen}
            className="px-2.5 py-1.5 rounded-sm border text-[0.72rem] font-mono text-qds-t1 hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all"
          >
            打开文件夹
          </button>
          <button
            onClick={onValidate}
            disabled={validating}
            className="px-2.5 py-1.5 rounded-sm border text-[0.72rem] font-mono text-qds-t1 hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all disabled:opacity-50 flex items-center gap-1.5"
          >
            {validating && (
              <span className="size-2.5 border border-current border-t-transparent rounded-full animate-spin" />
            )}
            验证
          </button>
          <button
            onClick={() => console.log("delete", name)}
            className="px-2.5 py-1.5 rounded-sm border text-[0.72rem] font-mono transition-all hover:bg-qds-danger-dim text-destructive border-destructive"
          >
            删除
          </button>
        </div>
      </div>

      {/* Validate result banner */}
      {validateResult && (
        <FadeIn>
          <div
            className="rounded-sm px-4 py-2.5 text-[0.68rem] font-medium border"
            style={{
              background: validateResult === "ok" ? "var(--suc-d)" : "var(--dan-d)",
              borderColor: `color-mix(in srgb, ${validateResult === "ok" ? "var(--suc)" : "var(--dan)"} 30%, transparent)`,
              color: validateResult === "ok" ? "var(--suc)" : "var(--dan)",
            }}
          >
            {validateResult === "ok" ? "验证通过" : `验证失败: ${validateResult}`}
          </div>
        </FadeIn>
      )}

      {/* Overview section */}
      <FadeIn>
        <SectionLabel>概览</SectionLabel>
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: "策略名称", value: info.name },
            { label: "策略类", value: info.class_name ?? "—" },
            { label: "类型", value: "策略" },
            { label: "版本", value: info.version ? `v${info.version}` : "—" },
            { label: "框架", value: "NautilusTrader" },
            { label: "交易所", value: "Binance" },
          ].map((item) => (
            <div
              key={item.label}
              className="border rounded-sm px-3 py-2.5 hover:border-qds-border-hover transition-colors bg-card"
            >
              <div className="text-[0.6rem] text-muted-foreground uppercase tracking-[0.05em] mb-1">{item.label}</div>
              <div className="text-[0.8rem] font-mono font-medium text-foreground truncate">{item.value}</div>
            </div>
          ))}
        </div>
        {info.file_path && (
          <div
            className="mt-3 border rounded-sm px-3 py-2.5 hover:border-qds-border-hover transition-colors bg-card"
          >
            <div className="text-[0.6rem] text-muted-foreground uppercase tracking-[0.05em] mb-1">文件路径</div>
            <div className="text-[0.8rem] font-mono font-medium text-foreground break-all">{info.file_path}</div>
          </div>
        )}
      </FadeIn>

      {/* Params section */}
      <FadeIn>
        <SectionLabel>
          参数
          {params.length > 0 && (
            <span className="text-muted-foreground font-normal tracking-normal normal-case text-[0.62rem]">
              · {params.length} 个
            </span>
          )}
        </SectionLabel>
        {paramsLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full bg-secondary" />
            ))}
          </div>
        ) : params.length === 0 ? (
          <div
            className="rounded-sm px-4 py-6 text-center text-[0.68rem] text-muted-foreground border bg-card"
          >
            暂无参数配置
          </div>
        ) : (
          <div
            className="rounded-sm border overflow-hidden bg-card"
          >
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>参数名</TableHead>
                  <TableHead>类型</TableHead>
                  <TableHead className="text-right">默认值</TableHead>
                  <TableHead className="text-right">优化范围</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {params.map((p) => (
                  <TableRow key={p.name}>
                    <TableCell className="font-medium">{p.name}</TableCell>
                    <TableCell>
                      <TypeBadge type={p.type} />
                    </TableCell>
                    <TableCell className="text-right font-medium">
                      {p.default !== undefined && p.default !== null ? String(p.default) : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      {p.min != null && p.max != null ? (
                        <span className="text-qds-t1">
                          [{p.min} → {p.max}]
                        </span>
                      ) : (
                        <span className="text-qds-t3">—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </FadeIn>

      {/* Version history */}
      {detail?.versions && detail.versions.length > 0 && (
        <FadeIn>
          <SectionLabel>版本历史</SectionLabel>
          <div className="relative pl-6">
            <div
              className="absolute left-[5px] top-[6px] bottom-[6px] w-px bg-border"
            />
            {detail.versions.map((v, i) => (
              <div key={v.version} className="relative pb-5 last:pb-0">
                <div
                  className={`absolute left-[-1.5rem] top-[5px] size-[11px] rounded-full border-2 ${
                    i === 0 ? "border-primary bg-primary" : "border bg-background"
                  }`}
                />
                <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                  <span className="text-[0.75rem] font-mono font-semibold text-foreground">
                    v{v.version}
                  </span>
                  {i === 0 && (
                    <span className="text-[0.56rem] px-1.5 py-0.5 rounded font-bold bg-qds-accent-dim text-primary">
                      latest
                    </span>
                  )}
                  {v.date && (
                    <span className="text-[0.62rem] font-mono text-qds-t3">{v.date}</span>
                  )}
                </div>
                {v.notes && (
                  <div className="text-[0.72rem] text-muted-foreground">{v.notes}</div>
                )}
              </div>
            ))}
          </div>
        </FadeIn>
      )}
    </div>
  );
}

/* -- List view ------------------------------------------------------ */

function ListView({
  strategies,
  loading,
  error,
  search,
  onSearchChange,
  onRowClick,
  onRescan,
  rescanning,
  onCreateOpen,
  count,
}: {
  strategies: StrategyListItem[];
  loading: boolean;
  error: string | null;
  search: string;
  onSearchChange: (v: string) => void;
  onRowClick: (s: StrategyListItem) => void;
  onRescan: () => void;
  rescanning: boolean;
  onCreateOpen: () => void;
  count: number;
}) {
  return (
    <div className="flex flex-col gap-5">
      {/* Top bar */}
      <div className="flex items-center justify-between pt-8 pb-2">
        <div>
          <h1 className="text-[1.1rem] font-bold font-mono tracking-tight text-foreground">策略管理</h1>
          <div className="text-[0.62rem] font-mono text-muted-foreground mt-0.5">
            {loading ? "加载中..." : `${count} 个策略`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onCreateOpen}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-sm border text-[0.72rem] font-mono text-qds-t1 hover:border-qds-border-hover hover:text-foreground hover:bg-secondary transition-all"
          >
            <Plus className="size-3" />
            新建策略
          </button>
          <button
            onClick={onRescan}
            disabled={rescanning}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-sm bg-primary text-[0.72rem] font-mono font-semibold text-white transition-all disabled:opacity-50"
          >
            <RefreshCw className={`size-3 ${rescanning ? "animate-spin" : ""}`} />
            重新扫描
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
        <input
          type="text"
          placeholder="搜索策略名称或类名..."
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          className="w-full h-9 rounded-sm border pl-9 pr-4 text-[0.75rem] font-mono text-foreground placeholder:text-qds-t3 outline-none focus:border-primary transition-colors bg-input"
        />
      </div>

      {/* List card */}
      <div
        className="rounded-lg border overflow-hidden bg-card"
      >
        {loading ? (
          <ListSkeleton />
        ) : error ? (
          <div className="px-6 py-10 text-center text-[0.72rem] text-destructive">{error}</div>
        ) : strategies.length === 0 ? (
          <div className="px-6 py-10 text-center text-[0.72rem] text-muted-foreground">
            {search ? "无匹配结果" : "暂无策略，请点击重新扫描"}
          </div>
        ) : (
          strategies.map((s) => (
            <StrategyRow key={s.name} strategy={s} onClick={() => onRowClick(s)} />
          ))
        )}
      </div>
    </div>
  );
}

/* -- Page ----------------------------------------------------------- */

export default function StrategiesPage() {
  const [view, setView] = useState<"list" | "detail">("list");

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

  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    loadStrategies();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function loadStrategies(autoSelectName?: string) {
    setLoading(true);
    setError(null);
    try {
      const raw = await apiGet<RawStrategyItem[]>("/api/strategies");
      if (raw) {
        const mapped = raw.map((s) => ({
          name: s.name,
          class_name: s.strategy_class,
          file_path: s.file_path,
          version: s.version,
          description: s.description,
        }));
        setStrategies(mapped);
        if (autoSelectName) {
          const found = mapped.find((s) => s.name === autoSelectName);
          if (found) handleSelect(found);
        }
      }
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
      /* ignore */
    } finally {
      setRescanning(false);
    }
  }

  async function handleCreate() {
    const name = createName.trim();
    if (!name) return;
    setCreating(true);
    setCreateError(null);
    try {
      await apiPost("/api/strategies/create", { name, type: "strategy" });
      setCreateOpen(false);
      setRescanning(true);
      try {
        await apiPost("/api/strategies/rescan");
        await loadStrategies(name);
      } finally {
        setRescanning(false);
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setCreateError("策略已存在");
      else if (err instanceof Error) setCreateError(err.message || "创建失败");
      else setCreateError("创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function handleSelect(s: StrategyListItem) {
    setSelected(s);
    setDetail(null);
    setParams([]);
    setValidateResult(null);
    setDetailLoading(true);
    setParamsLoading(true);
    setView("detail");
    try {
      const d = await apiGet<StrategyDetail>(`/api/strategies/${encodeURIComponent(s.name)}`);
      if (d) setDetail(d);
    } catch {
      /* ignore */
    } finally {
      setDetailLoading(false);
    }
    try {
      const resp = await apiGet<ParamsResponse>(`/api/strategies/${encodeURIComponent(s.name)}/params`);
      if (resp?.config_params) {
        setParams(
          resp.config_params.map((cp) => {
            const range = resp.optimize_ranges?.[cp.name];
            return {
              name: cp.name,
              type: cp.type,
              default: cp.default ?? undefined,
              min: range?.min,
              max: range?.max,
            };
          })
        );
      }
    } catch {
      /* ignore */
    } finally {
      setParamsLoading(false);
    }
  }

  function handleBack() {
    setView("list");
    setValidateResult(null);
  }

  async function handleOpen() {
    if (!selected) return;
    try {
      await apiPost(`/api/strategies/${encodeURIComponent(selected.name)}/open`);
    } catch {
      /* ignore */
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
      if (err instanceof ApiError && err.data && typeof err.data === "object") {
        const d = err.data as { issues?: string[] };
        setValidateResult(d.issues?.join("; ") ?? (err as Error).message);
      } else {
        setValidateResult(err instanceof Error ? err.message : "error");
      }
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
    <div className="flex flex-col h-full overflow-auto">
      <div className="max-w-[960px] w-full mx-auto px-8 pb-16">
        {view === "list" ? (
          <FadeIn key="list">
            <ListView
              strategies={filtered}
              loading={loading}
              error={error}
              search={search}
              onSearchChange={setSearch}
              onRowClick={handleSelect}
              onRescan={handleRescan}
              rescanning={rescanning}
              onCreateOpen={() => {
                setCreateName("");
                setCreateError(null);
                setCreateOpen(true);
              }}
              count={strategies.length}
            />
          </FadeIn>
        ) : selected ? (
          <FadeIn key="detail">
            <div className="pt-8">
              <DetailView
                strategy={selected}
                detail={detail}
                params={params}
                detailLoading={detailLoading}
                paramsLoading={paramsLoading}
                validating={validating}
                validateResult={validateResult}
                onBack={handleBack}
                onValidate={handleValidate}
                onOpen={handleOpen}
              />
            </div>
          </FadeIn>
        ) : null}
      </div>

      {/* Create Dialog */}
      <Dialog open={createOpen} onOpenChange={(open) => { if (!creating) setCreateOpen(open); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>新建策略</DialogTitle>
            <DialogDescription>选择策略类型并输入名称</DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-2">
              <span className="qds-stat-label">名称</span>
              <input
                placeholder="输入策略名称"
                value={createName}
                onChange={(e) => { setCreateName(e.target.value); setCreateError(null); }}
                onKeyDown={(e) => { if (e.key === "Enter" && createName.trim() && !creating) handleCreate(); }}
                className="h-9 rounded-sm border px-3 text-[0.72rem] text-foreground placeholder:text-qds-t3 outline-none focus:border-primary focus:shadow-[0_0_0_3px_var(--acc-d)] transition-all bg-input"
                style={{ transitionDuration: "var(--dur)" }}
              />
            </div>
            {createError && (
              <div
                className="rounded-sm px-4 py-2.5 text-[0.68rem] font-medium border bg-qds-danger-dim text-destructive"
                style={{ borderColor: "color-mix(in srgb, var(--dan) 30%, transparent)" }}
              >
                {createError}
              </div>
            )}
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" size="sm" />}>取消</DialogClose>
            <Button
              size="sm"
              onClick={handleCreate}
              disabled={!createName.trim() || creating}
              className="bg-primary text-white hover:opacity-90 disabled:opacity-50"
            >
              {creating && (
                <span className="size-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
              )}
              创建
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
