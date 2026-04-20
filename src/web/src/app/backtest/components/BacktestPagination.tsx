"use client";

interface BacktestPaginationProps {
  curPage: number;
  totalPages: number;
  total: number;
  pageSize: number;
  onPageChange: (p: number) => void;
}

/**
 * Compact page navigator for the backtest history list.
 *
 * Uses a 7-button window with ellipsis sentinels that hug the current page.
 */
export function BacktestPagination({ curPage, totalPages, total, pageSize, onPageChange }: BacktestPaginationProps) {
  const start = (curPage - 1) * pageSize + 1;
  const end = Math.min(curPage * pageSize, total);

  const maxBtns = 7;
  let pStart = Math.max(1, curPage - 3);
  const pEnd = Math.min(totalPages, pStart + maxBtns - 1);
  if (pEnd - pStart < maxBtns - 1) pStart = Math.max(1, pEnd - maxBtns + 1);

  const pagerBtnBase =
    "w-7 h-[26px] inline-flex items-center justify-center border border-border rounded-md bg-transparent text-muted-foreground cursor-pointer transition-all font-mono text-[0.68rem] hover:border-qds-border-hover hover:text-foreground hover:bg-secondary disabled:opacity-30 disabled:cursor-default disabled:pointer-events-none";
  const pagerBtnActive = "bg-primary/15 !border-primary !text-primary";

  const buttons: React.ReactNode[] = [];
  if (pStart > 1) {
    buttons.push(<button key="p1" className={pagerBtnBase} onClick={() => onPageChange(1)}>1</button>);
    if (pStart > 2) buttons.push(<span key="d1" className="w-[18px] text-center text-qds-t3 font-mono text-[0.68rem]">...</span>);
  }
  for (let i = pStart; i <= pEnd; i++) {
    buttons.push(
      <button key={i} className={`${pagerBtnBase} ${i === curPage ? pagerBtnActive : ""}`} onClick={() => onPageChange(i)}>{i}</button>,
    );
  }
  if (pEnd < totalPages) {
    if (pEnd < totalPages - 1) buttons.push(<span key="d2" className="w-[18px] text-center text-qds-t3 font-mono text-[0.68rem]">...</span>);
    buttons.push(<button key={`p${totalPages}`} className={pagerBtnBase} onClick={() => onPageChange(totalPages)}>{totalPages}</button>);
  }

  return (
    <div className="flex items-center justify-between px-3 py-2.5 border-t border-border font-mono text-[0.7rem] text-muted-foreground gap-3 flex-wrap">
      <span>{start}&ndash;{end} / {total}</span>
      <div className="flex items-center gap-[2px]">
        <button className={pagerBtnBase} disabled={curPage <= 1} onClick={() => onPageChange(1)}>&laquo;</button>
        <button className={pagerBtnBase} disabled={curPage <= 1} onClick={() => onPageChange(curPage - 1)}>&lsaquo;</button>
        {buttons}
        <button className={pagerBtnBase} disabled={curPage >= totalPages} onClick={() => onPageChange(totalPages)}>&rsaquo;</button>
        <button className={pagerBtnBase} disabled={curPage >= totalPages} onClick={() => onPageChange(totalPages)}>&raquo;</button>
      </div>
    </div>
  );
}
