"use client";

import { Card, CardContent } from "@/components/ui/card";

interface ParamsEchoProps {
  config?: Record<string, unknown>;
}

/**
 * Parameter echo block for the Cost & Params tab.
 *
 * Pulls the ``config`` object stored in ``FactorRun.config`` — the full
 * ``EvalConfig`` snapshot plus any user-supplied ``params`` override.
 * Rendered as a definition list; kept intentionally low-density because
 * it's pure reference data, not analytics.
 */
export function ParamsEcho({ config }: ParamsEchoProps) {
  if (!config || Object.keys(config).length === 0) {
    return (
      <Card padding={false} className="overflow-hidden">
        <CardContent className="p-4 text-[0.72rem] text-muted-foreground font-mono">
          无参数快照
        </CardContent>
      </Card>
    );
  }

  // Pull the well-known EvalConfig fields first, then surface user params.
  const knownKeys = [
    "universe",
    "start",
    "end",
    "forward_period",
    "quantiles",
    "cost_bps",
    "ic_freq",
    "log_ret",
  ] as const;

  const rows: { label: string; value: string; key: string }[] = [];
  for (const k of knownKeys) {
    if (k in config) {
      rows.push({ key: k, label: k, value: formatValue(config[k]) });
    }
  }

  // Factor params override is nested under config.params (if any).
  const factorParams = config.params;
  if (
    factorParams &&
    typeof factorParams === "object" &&
    !Array.isArray(factorParams)
  ) {
    for (const [k, v] of Object.entries(factorParams as Record<string, unknown>)) {
      rows.push({ key: `params.${k}`, label: `params.${k}`, value: formatValue(v) });
    }
  }

  return (
    <Card padding={false} className="overflow-hidden">
      <CardContent className="p-0">
        <table className="w-full font-mono text-[0.72rem]">
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.key}
                className="border-b last:border-b-0 hover:bg-secondary transition-colors duration-150"
              >
                <td className="px-4 py-2 text-muted-foreground w-40 align-top">
                  {r.label}
                </td>
                <td className="px-4 py-2 text-foreground break-all">
                  {r.value}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}

function formatValue(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    // Universe lists can be long — preview first 5.
    if (v.length > 5) {
      return `[${v.slice(0, 5).join(", ")}, …(+${v.length - 5})]`;
    }
    return `[${v.join(", ")}]`;
  }
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}
