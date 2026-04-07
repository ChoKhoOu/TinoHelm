import * as React from "react"

import { cn } from "@/lib/utils"

function Card({
  className,
  size = "default",
  padding = true,
  ...props
}: React.ComponentProps<"div"> & { size?: "default" | "sm"; padding?: boolean }) {
  return (
    <div
      data-slot="card"
      data-size={size}
      className={cn(
        "card flex flex-col gap-4 has-data-[slot=card-footer]:pb-0 has-[>img:first-child]:pt-0 *:[img:first-child]:rounded-t-xl *:[img:last-child]:rounded-b-xl",
        className
      )}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn(
        "card-header group/card-header grid auto-rows-min items-start gap-1 has-data-[slot=card-action]:grid-cols-[1fr_auto] has-data-[slot=card-description]:grid-rows-[auto_auto]",
        className
      )}
      {...props}
    />
  )
}

function CardTitle({ className, style, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-title"
      className={cn("leading-snug", className)}
      style={{ fontSize: ".8rem", fontWeight: 600, ...style }}
      {...props}
    />
  )
}

function CardDescription({ className, style, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-description"
      className={cn(className)}
      style={{ fontSize: ".68rem", color: "var(--t2)", ...style }}
      {...props}
    />
  )
}

function CardAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-action"
      className={cn(
        "col-start-2 row-span-2 row-start-1 self-start justify-self-end",
        className
      )}
      {...props}
    />
  )
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-content"
      className={cn("card-body", className)}
      {...props}
    />
  )
}

function CardFooter({ className, style, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-footer"
      className={cn("flex items-center", className)}
      style={{ borderTop: "1px solid var(--border-default)", padding: "1rem 1.5rem", ...style }}
      {...props}
    />
  )
}

interface MetricCardProps {
  label: string;
  value: string;
  change?: string;
  changeType?: "positive" | "negative" | "neutral";
  className?: string;
}

function MetricCard({ label, value, change, changeType = "neutral", className }: MetricCardProps) {
  const changeClass =
    changeType === "positive"
      ? "color-success"
      : changeType === "negative"
      ? "color-danger"
      : "";

  return (
    <div className={cn("stat-card flex flex-col gap-2", className)}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {change && (
        <span className={cn("stat-sub", changeClass)}>{change}</span>
      )}
    </div>
  );
}

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardAction,
  CardDescription,
  CardContent,
  MetricCard,
}
