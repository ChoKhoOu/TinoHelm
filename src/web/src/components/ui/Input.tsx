import * as React from "react"

import { cn } from "@/lib/utils"

interface InputProps extends React.ComponentProps<"input"> {
  label?: string;
}

function Input({ className, type, label, ...props }: InputProps) {
  const input = (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base transition-colors outline-none file:inline-flex file:h-6 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-input/50 disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:bg-input/30 dark:disabled:bg-input/80 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  );

  if (label) {
    return (
      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">{label}</label>
        {input}
      </div>
    );
  }
  return input;
}

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends React.ComponentProps<"select"> {
  label?: string;
  options?: SelectOption[] | string[];
  onValueChange?: (value: string) => void;
}

function Select({ className, label, options, onValueChange, onChange, children, ...props }: SelectProps) {
  const handleChange: React.ChangeEventHandler<HTMLSelectElement> = (e) => {
    onValueChange?.(e.target.value);
    onChange?.(e);
  };

  const select = (
    <select
      data-slot="select"
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-[var(--bg-card)] px-2.5 py-1 text-base transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        className
      )}
      onChange={onChange ? handleChange : undefined}
      {...props}
    >
      {options
        ? options.map((opt) => {
            const val = typeof opt === "string" ? opt : opt.value;
            const lbl = typeof opt === "string" ? opt : opt.label;
            return <option key={val} value={val}>{lbl}</option>;
          })
        : children}
    </select>
  );

  if (label) {
    return (
      <div className="flex flex-col gap-1">
        <label className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)] uppercase">{label}</label>
        {select}
      </div>
    );
  }
  return select;
}

export { Input, Select }
