"use client";

import { useState } from "react";
import { apiDelete } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { CatalogEntry, CATEGORY_LABELS } from "./types";

interface DeleteDialogProps {
  entry: CatalogEntry | null;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}

export function DeleteDialog({ entry, open, onClose, onDeleted }: DeleteDialogProps) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleConfirm() {
    if (!entry) return;
    setDeleting(true);
    setError(null);
    try {
      await apiDelete(`/api/data/catalog/${entry.id}`);
      onDeleted();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeleting(false);
    }
  }

  const typeLabel = entry ? (CATEGORY_LABELS[entry.data_type] || entry.data_type) : "";

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-card border sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle className="text-destructive">确认删除</DialogTitle>
        </DialogHeader>
        <div className="py-2 text-[12px] text-muted-foreground leading-relaxed">
          确定删除{" "}
          <span className="text-foreground font-semibold">{entry?.symbol}</span>{" "}
          的{" "}
          <span className="text-foreground font-semibold">{typeLabel}</span>{" "}
          ({entry?.interval}) 数据集？
          <br />
          <span className="text-destructive text-[11px]">
            此操作不可恢复，磁盘文件和数据库记录都将被删除。
          </span>
        </div>
        {error && (
          <span className="text-[11px] text-destructive">{error}</span>
        )}
        <DialogFooter>
          <Button
            variant="outline"
            onClick={onClose}
            className="h-8 text-[11px]"
          >
            取消
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={deleting}
            className="h-8 text-[11px] bg-[var(--dan)] text-input hover:bg-[var(--dan)]"
          >
            {deleting ? (
              <div className="w-3 h-3 border border-current border-t-transparent rounded-full animate-spin" />
            ) : (
              "确认删除"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
