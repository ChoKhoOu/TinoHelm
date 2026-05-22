"use client"

import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "qds-btn inline-flex shrink-0 items-center justify-center whitespace-nowrap ease-qds active:translate-y-px active:scale-[0.98] [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        default:     "qds-btn-primary hover:-translate-y-0.5 hover:shadow-md",
        secondary:   "qds-btn-secondary",
        ghost:       "qds-btn-ghost",
        destructive: "border border-destructive text-destructive hover:bg-destructive hover:text-white",
        outline:     "qds-btn-secondary",
        link:        "text-[var(--accent)] underline-offset-4 hover:underline !bg-transparent !border-transparent !shadow-none",
        warning:     "border border-qds-warning text-qds-warning hover:bg-qds-warning hover:text-black",
      },
      size: {
        default:   "",
        sm:        "!text-[.72rem] !py-[.35rem] !px-[.7rem]",
        lg:        "!text-[.85rem] !py-[.65rem] !px-[1.5rem]",
        icon:      "!p-[.45rem]",
        xs:        "!text-[.65rem] !py-[.25rem] !px-[.55rem]",
        "icon-xs": "!p-[.3rem]",
        "icon-sm": "!p-[.38rem]",
        "icon-lg": "!p-[.55rem]",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
}

export { Button, buttonVariants }
