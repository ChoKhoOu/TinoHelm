interface ToggleProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  "aria-label"?: string;
}

export function Toggle({ checked, onChange, label, "aria-label": ariaLabel }: ToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel ?? label}
      onClick={() => onChange(!checked)}
      className="flex items-center gap-2"
    >
      <div
        className={`relative w-9 h-5 rounded-full p-0.5 transition-colors duration-150 ${
          checked ? "bg-[var(--accent-green)]" : "bg-[var(--border-gray)]"
        }`}
      >
        <div
          className={`w-4 h-4 rounded-full bg-white transition-transform duration-150 ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </div>
      {label && (
        <span
          className={`text-[11px] font-medium ${
            checked ? "text-[var(--text-secondary)]" : "text-[var(--text-muted)]"
          }`}
        >
          {label}
        </span>
      )}
    </button>
  );
}
