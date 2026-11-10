import { Dialog as D, DropdownMenu as DM, Popover as P, Tooltip as T } from "radix-ui";
import { X } from "lucide-react";
import type * as React from "react";
import { cn } from "@/lib/utils";

const panel = "rounded-lg border border-line bg-surface shadow-pop";

/* Dialog ------------------------------------------------------------------------------- */
export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({
  className,
  children,
  title,
  description,
  ...props
}: React.ComponentProps<typeof D.Content> & { title: string; description?: string }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-ink/30" />
      <D.Content
        className={cn(
          panel,
          "fixed top-1/2 left-1/2 z-50 max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] max-w-[420px] -translate-x-1/2 -translate-y-1/2 overflow-y-auto p-6 focus:outline-none",
          className,
        )}
        {...props}
      >
        <div className="pr-8">
          <D.Title className="text-[16px] font-bold tracking-[-0.01em] text-ink">{title}</D.Title>
          {description ? (
            <D.Description className="mt-1.5 text-[13px] leading-relaxed text-muted">{description}</D.Description>
          ) : (
            <D.Description className="sr-only">{title}</D.Description>
          )}
        </div>
        {children}
        {/* last in DOM order, so the dialog's first action receives focus on open */}
        <D.Close className="absolute top-4 right-4 rounded-md p-1.5 text-muted hover:bg-subtle hover:text-ink" aria-label="Close">
          <X className="size-4" />
        </D.Close>
      </D.Content>
    </D.Portal>
  );
}

/* Dropdown menu ------------------------------------------------------------------------ */
export const Menu = DM.Root;
export const MenuTrigger = DM.Trigger;

export function MenuContent({ className, ...props }: React.ComponentProps<typeof DM.Content>) {
  return (
    <DM.Portal>
      <DM.Content sideOffset={6} align="end" collisionPadding={12} className={cn(panel, "z-50 min-w-60 p-1.5", className)} {...props} />
    </DM.Portal>
  );
}

export function MenuItem({ className, ...props }: React.ComponentProps<typeof DM.Item>) {
  return (
    <DM.Item
      className={cn(
        "flex h-9 cursor-default items-center gap-2.5 rounded-md px-2.5 text-[13px] font-medium text-ink outline-none select-none data-[disabled]:opacity-40 data-[highlighted]:bg-subtle [&_svg]:size-4 [&_svg]:text-muted",
        className,
      )}
      {...props}
    />
  );
}

export function MenuLabel({ className, ...props }: React.ComponentProps<typeof DM.Label>) {
  return <DM.Label className={cn("px-2.5 pt-2 pb-1 text-[11px] font-semibold text-muted", className)} {...props} />;
}

export function MenuSeparator() {
  return <DM.Separator className="-mx-1.5 my-1.5 h-px bg-line" />;
}

/* Popover ------------------------------------------------------------------------------ */
export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;

export function PopoverContent({ className, ...props }: React.ComponentProps<typeof P.Content>) {
  return (
    <P.Portal>
      <P.Content sideOffset={6} collisionPadding={12} className={cn(panel, "z-50 w-72 p-4 focus:outline-none", className)} {...props} />
    </P.Portal>
  );
}

/* Tooltip ------------------------------------------------------------------------------ */
export const TooltipProvider = T.Provider;

export function Tip({ content, children }: { content: React.ReactNode; children: React.ReactNode }) {
  return (
    <T.Root delayDuration={350}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          sideOffset={6}
          collisionPadding={8}
          className="z-50 max-w-72 rounded-md bg-ink px-2.5 py-1.5 text-[12px] leading-snug font-medium text-white"
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
