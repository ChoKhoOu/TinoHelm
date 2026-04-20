"use client";

import { useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { FactorGroup } from "./types";

interface CreateFactorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (groups: FactorGroup[]) => void;
}

export function CreateFactorDialog({ open, onOpenChange, onCreated }: CreateFactorDialogProps) {
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  async function handleCreate() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setCreating(true);
    setErr(null);
    try {
      await apiPost("/api/research/factors/create", { name: trimmed });
      const groups = await apiGet<FactorGroup[]>("/api/research/factors");
      if (groups && groups.length > 0) onCreated(groups);
      setName("");
      onOpenChange(false);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>新增自定义因子</DialogTitle>
          <DialogDescription>
            文件将创建在{" "}
            <span className="font-mono text-foreground">~/.tino/research/factors/</span>{" "}
            目录下，基于模板生成
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-1.5">
          <label className="font-mono text-[0.65rem] text-muted-foreground">
            因子名称（文件名）
          </label>
          <Input
            autoFocus
            placeholder="如 my_momentum"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && name.trim()) handleCreate();
            }}
          />
          <div className="font-mono text-[0.6rem] text-qds-t3">
            仅允许字母、数字、下划线，以字母或下划线开头
          </div>
        </div>

        {err && (
          <div className="font-mono text-[0.62rem] text-destructive bg-input rounded px-2 py-1.5">
            {err}
          </div>
        )}

        <DialogFooter>
          <DialogClose
            render={<Button variant="outline" size="sm">取消</Button>}
          />
          <Button
            variant="default"
            size="sm"
            disabled={!name.trim() || creating}
            onClick={handleCreate}
          >
            {creating ? "创建中..." : "创建"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
