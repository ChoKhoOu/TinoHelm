import { Badge } from "@/components/ui/badge";

/**
 * Maps verdict strings (pass / warn / fail) to a colored Badge, or renders an em-dash
 * for null/empty values. Replaces the legacy `.verdict / .verdict-pass / .verdict-warn /
 * .verdict-fail` pixel-perfect CSS family from globals.css.
 */
export function VerdictBadge({ value }: { value: string | null | undefined }) {
  if (!value || value === "—") {
    return <span className="text-muted-foreground text-[0.6rem]">—</span>;
  }

  const variantMap: Record<string, "success" | "warning" | "error" | "neutral"> = {
    pass: "success",
    warn: "warning",
    fail: "error",
  };
  const variant = variantMap[value] ?? "neutral";

  return <Badge variant={variant}>{value.toUpperCase()}</Badge>;
}

/** Semantic verdict badge for the result summary table (强 / 可用 / 弱). */
export function StrengthBadge({ ir }: { ir: number }) {
  if (ir >= 1) return <Badge variant="success">强</Badge>;
  if (ir >= 0.5) return <Badge variant="warning">可用</Badge>;
  return <Badge variant="error">弱</Badge>;
}
