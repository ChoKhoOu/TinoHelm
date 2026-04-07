"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AlertTriangle, ShieldAlert } from "lucide-react";

interface ConfirmModalProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  level: "warning" | "danger";
  title: string;
  description: string;
  confirmText?: string;
  confirmLabel?: string;
  loading?: boolean;
}

export function ConfirmModal({
  open,
  onClose,
  onConfirm,
  level,
  title,
  description,
  confirmText,
  confirmLabel,
  loading = false,
}: ConfirmModalProps) {
  const [inputValue, setInputValue] = useState("");
  const isDanger = level === "danger";
  const canConfirm = isDanger ? inputValue === confirmText : true;

  const handleClose = () => {
    setInputValue("");
    onClose();
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) handleClose(); }}>
      <DialogContent showCloseButton={false} className="max-w-[420px]">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div
              className={cn(
                "flex size-9 shrink-0 items-center justify-center rounded-sm",
                isDanger ? "bg-qds-danger-dim" : "bg-qds-warning-dim",
              )}
            >
              {isDanger ? (
                <ShieldAlert className="size-5 text-destructive" />
              ) : (
                <AlertTriangle className="size-5 text-qds-warning" />
              )}
            </div>
            <div className="flex flex-col gap-1">
              <DialogTitle>{title}</DialogTitle>
              <DialogDescription>{description}</DialogDescription>
            </div>
          </div>
        </DialogHeader>

        {isDanger && confirmText && (
          <div className="flex flex-col gap-2 px-0.5">
            <p className="text-[0.78rem] text-muted-foreground">
              请输入 <span className="font-mono font-semibold text-foreground">{confirmText}</span> 以确认
            </p>
            <Input
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder={confirmText}
              autoFocus
            />
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={handleClose} disabled={loading}>
            取消
          </Button>
          <Button
            variant={isDanger ? "destructive" : "default"}
            onClick={onConfirm}
            disabled={!canConfirm || loading}
            className={cn("relative", loading && "text-transparent pointer-events-none")}
          >
            {loading && (
              <span className="absolute inset-0 flex items-center justify-center">
                <span className="size-3.5 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
              </span>
            )}
            {confirmLabel ?? (isDanger ? "确认删除" : "确认")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
