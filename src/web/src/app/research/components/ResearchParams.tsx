"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { HelpTip, SectionLabel } from "@/components/qds";
import { ParamDivider, ParamNumberInput, ParamRow } from "./ParamRow";
import type { FactorDef } from "./types";

interface ResearchParamsProps {
  forwardPeriod: number;
  onForwardPeriodChange: (v: number) => void;
  quantiles: number;
  onQuantilesChange: (v: number) => void;
  returnType: string;
  onReturnTypeChange: (v: string) => void;
  selectedFactorDefs: FactorDef[];
  getParamValue: (factorName: string, key: string, defaultVal: number) => number;
  setParamValue: (factorName: string, key: string, value: number) => void;
}

export function ResearchParams({
  forwardPeriod,
  onForwardPeriodChange,
  quantiles,
  onQuantilesChange,
  returnType,
  onReturnTypeChange,
  selectedFactorDefs,
  getParamValue,
  setParamValue,
}: ResearchParamsProps) {
  return (
    <section className="mb-5">
      <SectionLabel>参数</SectionLabel>

      <ParamDivider>通用</ParamDivider>

      <ParamRow
        label={
          <>
            预测周期
            <HelpTip text="因子值预测未来 N 根 bar 的收益方向" />
          </>
        }
      >
        <ParamNumberInput
          value={forwardPeriod}
          onChange={onForwardPeriodChange}
          unit="bars"
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
        <ParamNumberInput value={quantiles} onChange={onQuantilesChange} unit="组" />
      </ParamRow>

      <ParamRow
        label={
          <>
            收益类型
            <HelpTip text="简单收益 = (P1-P0)/P0，对数收益 = ln(P1/P0)，短周期差异很小" />
          </>
        }
      >
        <Select value={returnType} onValueChange={(v) => v && onReturnTypeChange(v)}>
          <SelectTrigger className="w-[120px] h-7 text-[0.72rem]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="simple">简单收益</SelectItem>
            <SelectItem value="log">对数收益</SelectItem>
          </SelectContent>
        </Select>
      </ParamRow>

      {selectedFactorDefs.map(
        (f) =>
          f.params &&
          f.params.length > 0 && (
            <div key={f.name}>
              <ParamDivider>{f.name}</ParamDivider>
              {f.params.map((p) => (
                <ParamRow
                  key={p.key}
                  label={
                    <>
                      {p.label}
                      {p.tip && <HelpTip text={p.tip} />}
                    </>
                  }
                >
                  <ParamNumberInput
                    value={getParamValue(f.name, p.key, p.default)}
                    onChange={(v) => setParamValue(f.name, p.key, v)}
                    unit={p.unit}
                  />
                </ParamRow>
              ))}
            </div>
          ),
      )}
    </section>
  );
}
