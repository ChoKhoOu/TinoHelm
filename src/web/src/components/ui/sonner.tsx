"use client"

import { Toaster as Sonner, type ToasterProps } from "sonner"

function Toaster({ ...props }: ToasterProps) {
  return (
    <Sonner
      className="toaster group"
      theme="dark"
      position="bottom-right"
      visibleToasts={3}
      duration={5000}
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-card group-[.toaster]:text-qds-t1 group-[.toaster]:border group-[.toaster]:shadow-lg group-[.toaster]:rounded-[10px]",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-primary group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
          error: "",
          success: "",
        },
      }}
      {...props}
    />
  )
}

export { Toaster }
