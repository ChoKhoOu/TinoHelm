"use client"

import { Toaster as Sonner, type ToasterProps } from "sonner"

function Toaster({ ...props }: ToasterProps) {
  return (
    <Sonner
      className="toaster group"
      theme="dark"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-popover group-[.toaster]:text-popover-foreground group-[.toaster]:border-border group-[.toaster]:shadow-lg group-[.toaster]:rounded-lg",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
          error: "group-[.toaster]:!bg-destructive/10 group-[.toaster]:!text-destructive group-[.toaster]:!border-destructive/30",
          success: "group-[.toaster]:!bg-[var(--accent-green-20)] group-[.toaster]:!text-[var(--accent-green)] group-[.toaster]:!border-[var(--accent-green)]/30",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
