import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-[5px] px-1.5 text-[11px] font-semibold [&_svg]:size-3",
  {
    variants: {
      tone: {
        neutral: "bg-subtle text-ink-soft",
        outline: "border border-line text-muted",
        accent: "bg-accent-soft text-accent-hover",
        ok: "bg-ok-soft text-ok",
        no: "bg-no-soft text-no",
        solid: "bg-accent text-white",
      },
    },
    defaultVariants: { tone: "neutral" },
  },
);

export function Badge({
  className,
  tone,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
