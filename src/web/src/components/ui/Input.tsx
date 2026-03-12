import { InputHTMLAttributes, useId } from "react";
import { Search, ChevronDown } from "lucide-react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  variant?: "text" | "search";
}

export function Input({ label, variant = "text", className = "", id: externalId, ...props }: InputProps) {
  const generatedId = useId();
  const inputId = externalId ?? generatedId;

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={inputId} className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
          {label}
        </label>
      )}
      <div className="flex items-center gap-2 rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] px-[14px] py-[10px] focus-within:border-[var(--accent-green)] transition-colors duration-150">
        {variant === "search" && <Search className="w-3.5 h-3.5 text-[var(--text-muted)]" />}
        <input
          id={inputId}
          className={`w-full bg-transparent text-[11px] font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] outline-none ${className}`}
          {...props}
        />
      </div>
    </div>
  );
}

interface SelectProps {
  label?: string;
  value?: string;
  options: { value: string; label: string }[];
  onChange?: (value: string) => void;
  className?: string;
  id?: string;
}

export function Select({ label, value, options, onChange, className = "", id: externalId }: SelectProps) {
  const generatedId = useId();
  const selectId = externalId ?? generatedId;

  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      {label && (
        <label htmlFor={selectId} className="text-[10px] font-semibold tracking-[0.5px] text-[var(--text-muted)]">
          {label}
        </label>
      )}
      <div className="relative">
        <select
          id={selectId}
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          className="w-full appearance-none rounded-lg bg-[var(--bg-card)] border border-[var(--border-gray)] px-[14px] py-[10px] pr-10 text-[11px] font-medium text-[var(--text-primary)] outline-none focus:border-[var(--accent-green)] transition-colors duration-150"
        >
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-muted)] pointer-events-none" />
      </div>
    </div>
  );
}
