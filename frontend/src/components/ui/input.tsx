import * as React from "react";
import { cn } from "@/lib/utils";

const field =
  "w-full rounded-md border border-line bg-surface text-[13px] text-ink placeholder:text-faint focus-visible:border-amber focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber/25";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(field, "h-8 px-2.5", className)} {...props} />
  ),
);
Input.displayName = "Input";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(field, "resize-none px-2.5 py-2", className)} {...props} />
));
Textarea.displayName = "Textarea";

export const NativeSelect = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement>
>(({ className, ...props }, ref) => (
  <select ref={ref} className={cn(field, "h-8 px-2", className)} {...props} />
));
NativeSelect.displayName = "NativeSelect";
