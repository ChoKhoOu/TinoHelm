import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-full border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground",
        secondary: "bg-secondary text-secondary-foreground",
        destructive: "bg-destructive/10 text-destructive",
        outline: "border-border text-foreground",
        ghost: "bg-muted text-muted-foreground",
        link: "text-primary underline-offset-4 hover:underline",
        success: "bg-[var(--accent-green-20)] text-[var(--accent-green)] border-[var(--accent-green-20)]",
        warning: "bg-[var(--accent-amber-20)] text-[var(--accent-amber)] border-[var(--accent-amber-20)]",
        error: "bg-[var(--accent-red-20)] text-[var(--accent-red)] border-[var(--accent-red-20)]",
        info: "bg-[var(--accent-blue-20)] text-[var(--accent-blue)] border-[var(--accent-blue-20)]",
        connected: "bg-[var(--accent-green-20)] text-[var(--accent-green)] border-[var(--accent-green-20)]",
        disconnected: "bg-[var(--accent-red-20)] text-[var(--accent-red)] border-[var(--accent-red-20)]",
        neutral: "bg-muted text-muted-foreground border-transparent",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  dot,
  children,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { dot?: boolean }) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    >
      {dot !== false && dot && (
        <span className="inline-block w-1.5 h-1.5 rounded-full bg-current mr-0.5" />
      )}
      {children}
    </span>
  )
}

export { Badge, badgeVariants }
