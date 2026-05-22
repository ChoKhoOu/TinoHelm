import * as React from "react"

import { cn } from "@/lib/utils"

interface InputProps extends React.ComponentProps<"input"> {
  label?: string;
  hint?: string;
  error?: boolean;
}

function Input({ className, type, label, hint, error, ...props }: InputProps) {
  const input = (
    <input
      type={type}
      data-slot="input"
      aria-invalid={error || undefined}
      className={cn("qds-input", className)}
      {...props}
    />
  );

  if (label || hint) {
    return (
      <div className="flex flex-col gap-[.4rem]">
        {label && <label className="qds-label">{label}</label>}
        {input}
        {hint && <span className="qds-hint" data-error={error || undefined}>{hint}</span>}
      </div>
    );
  }
  return input;
}

export { Input }
