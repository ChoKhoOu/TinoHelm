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
            "group toast group-[.toaster]:bg-card group-[.toaster]:text-qds-t1 group-[.toaster]:border group-[.toaster]:shadow-lg group-[.toaster]:rounded-[10px]",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
          error: "group-[.toaster]:!bg-qds-danger-dim group-[.toaster]:!text-destructive group-[.toaster]:!border-destructive/30",
          success: "group-[.toaster]:!bg-qds-success-dim group-[.toaster]:!text-qds-success group-[.toaster]:!border-qds-success/30",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
