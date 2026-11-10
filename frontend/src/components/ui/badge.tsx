import { cva, type VariantProps } from "class-variance-authority";
import type * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 whitespace-nowrap rounded-sm px-1.5 py-px text-[11px] font-medium leading-[18px] [&_svg]:size-3",
  {
    variants: {
      tone: {
        neutral: "bg-sunken text-ink-soft",
        outline: "border border-line text-muted",
        amber: "bg-amber-soft text-amber-ink",
        ok: "bg-ok-soft text-ok",
        no: "bg-no-soft text-no",
        ink: "bg-ink text-paper",
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
