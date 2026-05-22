"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { HelpTip, SectionLabel } from "@/components/qds";
import type { FactorParams, FactorSpec } from "./types";

interface ParamsPanelProps {
  /** Selected factor spec — drives the dynamic ``params_schema`` form. */
  factor: FactorSpec | null;

  /** EvalConfig parameters (top-level, not factor-specific). */
  forwardPeriod: number;
  onForwardPeriodChange: (v: number) => void;
  quantiles: number;
  onQuantilesChange: (v: number) => void;
  icFreq: string;
  onIcFreqChange: (v: string) => void;
  costBps: number;
  onCostBpsChange: (v: number) => void;
  logRet: boolean;
  onLogRetChange: (v: boolean) => void;

  /** Factor-specific params (overrides ``FactorSpec.params_schema``). */
  factorParams: FactorParams;
  onFactorParamsChange: (params: FactorParams) => void;
}

/**
 * Divider dot + small uppercase label for a param group — matches Web UI Kit
 * ``.tok`` / param-group pattern used throughout the product.
 */
function ParamDivider({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 font-mono text-[0.6rem] text-muted-foreground mt-3 mb-1.5 uppercase tracking-wider">
      <span className="w-1 h-1 rounded-full bg-primary" />
      {children}
    </div>
  );
}

function ParamRow({
  label,
  children,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between py-1">
      <Label className="flex items-center font-mono text-[0.68rem] text-muted-foreground cursor-default">
        {label}
      </Label>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  );
}

function NumberInput({
  value,
  onChange,
  unit,
  step,
  min,
  max,
  testId,
}: {
  value: number;
  onChange: (v: number) => void;
  unit?: string;
  step?: number;
  min?: number;
  max?: number;
  testId?: string;
}) {
  return (
    <>
      <Input
        type="number"
        value={Number.isFinite(value) ? value : ""}
        step={step}
        min={min}
        max={max}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isFinite(n)) onChange(n);
        }}
        data-testid={testId}
        className="w-16 h-7 text-[0.72rem] px-2 py-1"
      />
      {unit && (
        <span className="font-mono text-[0.62rem] text-muted-foreground">
          {unit}
        </span>
      )}
    </>
  );
}

/**
 * Render dynamic factor-specific parameter controls from ``params_schema``.
 *
 * Numeric values use ``<NumberInput>``; booleans use a native checkbox; strings
 * fall back to a plain text input.  Default comes from ``FactorSpec`` and
 * overrides are held in the ``factorParams`` state.
 */
function FactorParamFields({
  schema,
  params,
  onChange,
}: {
  schema: FactorParams;
  params: FactorParams;
  onChange: (p: FactorParams) => void;
}) {
  const keys = Object.keys(schema);
  if (keys.length === 0) {
    return (
      <div className="font-mono text-[0.62rem] text-qds-t3 py-1">
        当前因子无可调参数
      </div>
    );
  }

  const set = (key: string, value: number | string | boolean) =>
    onChange({ ...params, [key]: value });

  return (
    <>
      {keys.map((key) => {
        const defaultVal = schema[key];
        const current = params[key] ?? defaultVal;
        const testId = `factor-param-${key}`;

        if (typeof defaultVal === "number") {
          return (
            <ParamRow key={key} label={key}>
              <NumberInput
                value={typeof current === "number" ? current : Number(current)}
                onChange={(v) => set(key, v)}
                testId={testId}
              />
            </ParamRow>
          );
        }

        if (typeof defaultVal === "boolean") {
          return (
            <ParamRow key={key} label={key}>
              <input
                type="checkbox"
                checked={Boolean(current)}
                onChange={(e) => set(key, e.target.checked)}
                data-testid={testId}
                className="accent-primary"
              />
            </ParamRow>
          );
        }

        return (
          <ParamRow key={key} label={key}>
            <Input
              type="text"
              value={String(current)}
              onChange={(e) => set(key, e.target.value)}
              data-testid={testId}
              className="w-24 h-7 text-[0.72rem] px-2 py-1"
            />
          </ParamRow>
        );
      })}
    </>
  );
}

export function ParamsPanel({
  factor,
  forwardPeriod,
  onForwardPeriodChange,
  quantiles,
  onQuantilesChange,
  icFreq,
  onIcFreqChange,
  costBps,
  onCostBpsChange,
  logRet,
  onLogRetChange,
  factorParams,
  onFactorParamsChange,
}: ParamsPanelProps) {
  return (
    <section className="mb-5">
      <SectionLabel>参数</SectionLabel>

      <ParamDivider>通用 · EvalConfig</ParamDivider>

      <ParamRow
        label={
          <>
            预测周期
            <HelpTip text="因子值预测未来 N 根 bar 的收益方向" />
          </>
        }
      >
        <NumberInput
          value={forwardPeriod}
          onChange={onForwardPeriodChange}
          unit="bars"
          min={1}
          testId="forward-period"
        />
      </ParamRow>

      <ParamRow
        label={
          <>
            分层数量
            <HelpTip text="按因子值从高到低分成 N 组，观察各组收益差异" />
          </>
        }
      >
        <NumberInput
          value={quantiles}
          onChange={onQuantilesChange}
          unit="组"
          min={2}
          max={10}
          testId="quantiles"
        />
      </ParamRow>

      <ParamRow
        label={
          <>
            IC 频率
            <HelpTip text='"D" 日频 / "W" 周频 / "H" 小时频' />
          </>
        }
      >
        <Select value={icFreq} onValueChange={(v) => v && onIcFreqChange(v)}>
          <SelectTrigger
            className="w-[96px] h-7 text-[0.72rem]"
            data-testid="ic-freq"
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="D">日频 D</SelectItem>
            <SelectItem value="W">周频 W</SelectItem>
            <SelectItem value="H">小时 H</SelectItem>
          </SelectContent>
        </Select>
      </ParamRow>

      <ParamRow
        label={
          <>
            手续费 (bps)
            <HelpTip text="单边手续费基点，影响换手成本估算" />
          </>
        }
      >
        <NumberInput
          value={costBps}
          onChange={onCostBpsChange}
          unit="bps"
          step={0.5}
          min={0}
          testId="cost-bps"
        />
      </ParamRow>

      <ParamRow
        label={
          <>
            对数收益
            <HelpTip text="启用时使用 log(P1/P0)，关闭时使用 (P1-P0)/P0" />
          </>
        }
      >
        <input
          type="checkbox"
          checked={logRet}
          onChange={(e) => onLogRetChange(e.target.checked)}
          data-testid="log-ret"
          className="accent-primary"
        />
      </ParamRow>

      {factor && (
        <>
          <ParamDivider>{factor.name} · 因子参数</ParamDivider>
          <FactorParamFields
            schema={factor.params_schema}
            params={factorParams}
            onChange={onFactorParamsChange}
          />
        </>
      )}
    </section>
  );
}
