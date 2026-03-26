"use client"

import * as React from "react"
import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group"

import { cn } from "@/lib/utils"

const ToggleGroupContext = React.createContext<{
  size?: "default" | "sm" | "lg"
  variant?: "default" | "outline"
}>({
  size: "default",
  variant: "default",
})

const toggleGroupItemVariants = (
  variant: "default" | "outline" = "default",
  size: "default" | "sm" | "lg" = "default"
) => {
  const base =
    "inline-flex items-center justify-center whitespace-nowrap text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
  const variantClass =
    variant === "outline"
      ? "border border-input bg-transparent hover:bg-accent hover:text-accent-foreground data-[state=on]:bg-accent data-[state=on]:text-accent-foreground"
      : "bg-transparent hover:bg-muted hover:text-muted-foreground data-[state=on]:bg-accent data-[state=on]:text-accent-foreground"
  const sizeClass =
    size === "sm"
      ? "h-7 px-2 text-xs rounded-md"
      : size === "lg"
        ? "h-10 px-4 rounded-md"
        : "h-8 px-3 rounded-md"
  return cn(base, variantClass, sizeClass)
}

const ToggleGroup = React.forwardRef<
  React.ComponentRef<typeof ToggleGroupPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Root> & {
    variant?: "default" | "outline"
    size?: "default" | "sm" | "lg"
  }
>(({ className, variant, size, children, ...props }, ref) => (
  <ToggleGroupPrimitive.Root
    ref={ref}
    className={cn(
      "inline-flex items-center justify-center gap-0.5 rounded-lg bg-muted/50 p-0.5",
      className
    )}
    {...props}
  >
    <ToggleGroupContext.Provider value={{ variant, size }}>
      {children}
    </ToggleGroupContext.Provider>
  </ToggleGroupPrimitive.Root>
))

ToggleGroup.displayName = ToggleGroupPrimitive.Root.displayName

const ToggleGroupItem = React.forwardRef<
  React.ComponentRef<typeof ToggleGroupPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Item> & {
    variant?: "default" | "outline"
    size?: "default" | "sm" | "lg"
  }
>(({ className, variant, size, ...props }, ref) => {
  const context = React.useContext(ToggleGroupContext)

  return (
    <ToggleGroupPrimitive.Item
      ref={ref}
      className={cn(
        toggleGroupItemVariants(
          variant || context.variant,
          size || context.size
        ),
        className
      )}
      {...props}
    />
  )
})

ToggleGroupItem.displayName = ToggleGroupPrimitive.Item.displayName

export { ToggleGroup, ToggleGroupItem }
