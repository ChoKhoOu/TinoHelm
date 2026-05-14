"use client";

import { useEffect } from "react";
import { apiDelete } from "@/lib/api";
import { useAction } from "@/hooks/use-action";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { CatalogEntry, SOURCE_TYPE_LABELS, formatBytes } from "./types";

interface DeleteDialogProps {
  entry: CatalogEntry | null;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}

export function DeleteDialog({ entry, open, onClose, onDeleted }: DeleteDialogProps) {
  const deleteAction = useAction(
    () => apiDelete(`/api/data/catalog/${encodeURIComponent(entry!.id)}`),
    { onSuccess: () => { onDeleted(); setTimeout(onClose, 800); }, successDuration: 800 }
  );

  useEffect(() => {
    if (!open) deleteAction.reset();
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const typeLabel = entry ? (SOURCE_TYPE_LABELS[entry.source_type || entry.data_type] ?? (entry.source_type || entry.data_type)) : "";

  const infoRows = entry ? [
    { label: "品种", value: entry.symbol },
    { label: "类型", value: typeLabel },
    { label: "记录数", value: entry.record_count != null ? `${(entry.record_count / 1_000_000).toFixed(2)}M` : "—" },
    { label: "释放空间", value: formatBytes(entry.size_bytes), cls: "text-qds-success" },
  ] : [];

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border sm:max-w-[420px] p-0 overflow-hidden">
        {/* Header */}
        <div style={{ padding: "1rem 1.25rem .75rem", display: "flex", alignItems: "flex-start", gap: ".75rem" }}>
          <div
            className="w-9 h-9 rounded-[10px] flex items-center justify-center flex-shrink-0 text-base"
            style={{ background: "var(--dan-d)", color: "var(--dan)" }}
          >✕</div>
          <div>
            <div className="text-[.9rem] font-semibold mb-[.15rem]">
              删除 {entry?.symbol} {typeLabel}?
            </div>
            <div className="text-[.75rem] text-muted-foreground leading-relaxed">
              此操作将删除 DB 记录和磁盘文件，不可恢复。
            </div>
          </div>
        </div>

        {/* Info rows */}
        <div style={{ padding: "0 1.25rem 1rem", display: "flex", flexDirection: "column", gap: ".35rem" }}>
          {infoRows.map((r) => (
            <div key={r.label} style={{
              display: "flex", justifyContent: "space-between",
              padding: ".35rem .5rem", background: "var(--bg-in)", borderRadius: "4px",
            }} className="font-mono text-[.72rem]">
              <span className="text-muted-foreground">{r.label}</span>
              <span className={r.cls ?? ""}>{r.value}</span>
            </div>
          ))}
          {deleteAction.state === "error" && deleteAction.error && (
            <div className="text-[.72rem] text-destructive mt-1">✕ {deleteAction.error}</div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: ".75rem 1.25rem", borderTop: "1px solid var(--bd)", display: "flex", justifyContent: "flex-end", gap: ".5rem" }}>
          <button className="btn btn-o" onClick={onClose}>取消</button>
          <button
            className={deleteAction.state === "success" ? "btn btn-p" : "btn btn-d"}
            onClick={() => deleteAction.execute()}
            disabled={deleteAction.state === "loading"}
          >
            {deleteAction.state === "loading" && "删除中..."}
            {deleteAction.state === "success" && "✓ 已删除"}
            {deleteAction.state === "error" && "✕ 失败"}
            {deleteAction.state === "idle" && "永久删除"}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
