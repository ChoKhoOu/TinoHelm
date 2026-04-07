import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "qds-badge inline-flex h-5 w-fit shrink-0 items-center justify-center gap-1 overflow-hidden rounded-full border border-transparent px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-all [&>svg]:pointer-events-none [&>svg]:size-3",
  {
    variants: {
      variant: {
        default: "bg-secondary text-qds-t1",
        secondary: "bg-secondary text-qds-t1",
        destructive: "bg-qds-danger-dim text-destructive",
        outline: "border text-qds-t1",
        ghost: "bg-muted text-muted-foreground",
        link: "text-primary underline-offset-4 hover:underline",
        success: "bg-qds-success-dim text-qds-success border-qds-success-dim",
        warning: "bg-qds-warning-dim text-qds-warning border-qds-warning-dim",
        error: "bg-qds-danger-dim text-destructive border-qds-danger-dim",
        info: "bg-qds-info-dim text-qds-info border-qds-info-dim",
        connected: "bg-qds-success-dim text-qds-success border-qds-success-dim",
        disconnected: "bg-qds-danger-dim text-destructive border-qds-danger-dim",
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
