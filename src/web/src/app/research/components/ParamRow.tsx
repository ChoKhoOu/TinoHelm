"use client";

import type { ReactNode } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Params section divider — small dot + small caps label above a param group. */
export function ParamDivider({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 font-mono text-[0.6rem] text-muted-foreground mt-3 mb-1.5 uppercase tracking-wider">
      <span className="w-1 h-1 rounded-full bg-primary" />
      {children}
    </div>
  );
}

interface ParamRowProps {
  label: ReactNode;
  children: ReactNode;
}

/** Single key-value row used by the research config params. */
export function ParamRow({ label, children }: ParamRowProps) {
  return (
    <div className="flex items-center justify-between py-1">
      <Label className="flex items-center font-mono text-[0.68rem] text-muted-foreground cursor-default">
        {label}
      </Label>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  );
}

interface ParamNumberInputProps {
  value: number;
  onChange: (value: number) => void;
  unit?: string;
}

/** Numeric input + optional unit suffix used inside a param row. */
export function ParamNumberInput({ value, onChange, unit }: ParamNumberInputProps) {
  return (
    <>
      <Input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-16 h-7 text-[0.72rem] px-2 py-1"
      />
      {unit && <span className="font-mono text-[0.62rem] text-muted-foreground">{unit}</span>}
    </>
  );
}
