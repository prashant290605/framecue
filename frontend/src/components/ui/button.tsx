import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-[13px] font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-ink text-paper hover:bg-ink-soft",
        secondary: "border border-line bg-surface text-ink shadow-card hover:bg-sunken",
        ghost: "text-ink-soft hover:bg-sunken hover:text-ink",
        accent: "bg-amber-soft text-amber-ink hover:bg-[#f1dfbd]",
        danger: "bg-no text-white hover:bg-[#8a2f25]",
        link: "h-auto px-0 text-amber-ink underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-7 px-2.5",
        md: "h-8 px-3",
        lg: "h-10 px-4 text-sm",
        icon: "size-8",
        "icon-sm": "size-7",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot.Root : "button";
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : (type ?? "button")}
        className={cn(buttonVariants({ variant, size }), className)}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
