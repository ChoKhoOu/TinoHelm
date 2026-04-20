"use client";

import { ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionLabel } from "@/components/qds";
import { cn } from "@/lib/utils";
import { irColor, timeAgo } from "./types";
import type { HistoryJob } from "./types";
import { VerdictBadge } from "./VerdictBadge";

interface ResearchJobQueueProps {
  histJobs: HistoryJob[];
  histLoading: boolean;
  histPage: number;
  histSize: number;
  onPageChange: (page: number) => void;
}

export function ResearchJobQueue({
  histJobs,
  histLoading,
  histPage,
  histSize,
  onPageChange,
}: ResearchJobQueueProps) {
  const total = histJobs.length;
  const pages = Math.max(1, Math.ceil(total / histSize));
  const safe = Math.min(histPage, pages);
  const slice = histJobs.slice((safe - 1) * histSize, safe * histSize);

  return (
    <div className="mb-6">
      <div className="flex justify-between items-center mb-2">
        <SectionLabel>
          历史诊断报告
          {total > 0 && (
            <span className="font-normal text-muted-foreground normal-case tracking-normal">
              · {total} 个
            </span>
          )}
        </SectionLabel>

        {pages > 1 && (
          <div className="flex items-center gap-2 font-mono text-[0.7rem] text-muted-foreground">
            <span>
              {(safe - 1) * histSize + 1}–{Math.min(safe * histSize, total)} / {total}
            </span>
            <Button
              variant="ghost"
              size="icon-xs"
              disabled={safe <= 1}
              onClick={() => onPageChange(safe - 1)}
            >
              <ChevronLeft />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              disabled={safe >= pages}
              onClick={() => onPageChange(safe + 1)}
            >
              <ChevronRight />
            </Button>
          </div>
        )}
      </div>

      <div className="rounded-lg border bg-card overflow-hidden">
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>因子</TableHead>
                <TableHead>品种</TableHead>
                <TableHead>状态</TableHead>
                <TableHead className="text-right">IR</TableHead>
                <TableHead>Profile</TableHead>
                <TableHead>Predict</TableHead>
                <TableHead>Robust</TableHead>
                <TableHead>Cost</TableHead>
                <TableHead className="text-right">时间</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {histLoading ? (
                <TableRow>
                  <TableCell colSpan={9} className="text-center text-muted-foreground py-6">
                    加载中...
                  </TableCell>
                </TableRow>
              ) : slice.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="text-center text-muted-foreground py-6">
                    暂无诊断报告
                  </TableCell>
                </TableRow>
              ) : (
                slice.map((job) => (
                  <TableRow
                    key={job.id}
                    className={cn(
                      job.status === "failed" && "opacity-65",
                      (job.status === "completed" || job.status === "failed") &&
                        "cursor-pointer",
                    )}
                  >
                    <TableCell>
                      <div className="font-semibold">{job.factor}</div>
                      {job.status === "failed" && job.error_msg && (
                        <div className="text-[0.58rem] text-destructive mt-0.5">
                          {job.error_msg}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {job.symbol} · {job.interval}
                    </TableCell>
                    <TableCell>
                      {job.status === "running" && (
                        <span className="inline-flex items-center gap-1">
                          <Loader2 className="animate-spin w-3 h-3 text-muted-foreground" />
                          <span className="font-mono text-[0.65rem] text-primary">
                            {job.progress ?? 0}%
                          </span>
                        </span>
                      )}
                      {job.status === "completed" && (
                        <Badge variant="success">完成</Badge>
                      )}
                      {job.status === "failed" && <Badge variant="error">失败</Badge>}
                    </TableCell>
                    <TableCell
                      className={cn("text-right font-semibold", irColor(job.ir))}
                    >
                      {job.ir != null ? job.ir.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell>
                      <VerdictBadge value={job.profile} />
                    </TableCell>
                    <TableCell>
                      <VerdictBadge value={job.predict} />
                    </TableCell>
                    <TableCell>
                      <VerdictBadge value={job.robust} />
                    </TableCell>
                    <TableCell>
                      <VerdictBadge value={job.cost} />
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {timeAgo(job.created_at)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>
    </div>
  );
}
