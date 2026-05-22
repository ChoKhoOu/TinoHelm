"use client"

import { Switch } from "@/components/ui/switch"

interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  "aria-label"?: string;
}

export function Toggle({ checked, onChange, label, "aria-label": ariaLabel }: ToggleProps) {
  return (
    <label className="flex items-center gap-2 cursor-pointer">
      <Switch
        checked={checked}
        onCheckedChange={onChange}
        aria-label={ariaLabel ?? label}
      />
      {label && (
        <span
          className={`text-[11px] font-medium ${
            checked ? "text-secondary-foreground" : "text-muted-foreground"
          }`}
        >
          {label}
        </span>
      )}
    </label>
  );
}
