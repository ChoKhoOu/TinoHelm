import * as React from "react"

import { cn } from "@/lib/utils"

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn("qds-input min-h-16 field-sizing-content", className)}
      {...props}
    />
  )
}

export { Textarea }
