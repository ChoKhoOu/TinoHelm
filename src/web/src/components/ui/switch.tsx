"use client"

import { cn } from "@/lib/utils"
import { Switch as SwitchPrimitive } from "@base-ui/react/switch"

function Switch({
  className,
  ...props
}: SwitchPrimitive.Root.Props) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn("qds-switch", className)}
      {...props}
    >
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
      />
    </SwitchPrimitive.Root>
  )
}

export { Switch }
