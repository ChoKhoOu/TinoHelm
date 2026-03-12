import { ButtonHTMLAttributes, ReactNode } from "react";

type ButtonVariant = "primary" | "secondary" | "danger" | "outline" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
  children: ReactNode;
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--accent-green)] text-[var(--text-on-accent)] hover:opacity-90",
  secondary:
    "bg-[var(--bg-card)] text-[var(--text-secondary)] border border-[var(--border-gray)] hover:border-[var(--border-light)]",
  danger: "bg-[var(--accent-red)] text-white hover:opacity-90",
  outline:
    "bg-[var(--bg-elevated)] text-[var(--accent-green)] border border-[var(--accent-green)] hover:bg-[var(--accent-green-10)]",
  ghost:
    "text-[var(--accent-green)] hover:bg-[var(--accent-green-10)]",
};

export function Button({
  variant = "primary",
  icon,
  children,
  className = "",
  ...props
}: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-5 py-[10px] text-[11px] font-bold tracking-wide transition-all duration-150 disabled:opacity-50 disabled:cursor-not-allowed ${variantClasses[variant]} ${className}`}
      {...props}
    >
      {icon && <span className="w-3 h-3">{icon}</span>}
      {children}
    </button>
  );
}
