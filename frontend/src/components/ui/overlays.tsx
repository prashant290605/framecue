import { Dialog as D, DropdownMenu as DM, Popover as P, Tooltip as T } from "radix-ui";
import { X } from "lucide-react";
import type * as React from "react";
import { cn } from "@/lib/utils";

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
      <D.Overlay className="fixed inset-0 z-50 bg-ink/25" />
      <D.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] max-w-md -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-line bg-surface p-5 shadow-pop focus:outline-none",
          className,
        )}
        {...props}
      >
        <div className="mb-3 flex items-start justify-between gap-4">
          <div>
            <D.Title className="font-display text-[17px] font-semibold text-ink">{title}</D.Title>
            {description ? (
              <D.Description className="mt-1 text-[13px] text-muted">{description}</D.Description>
            ) : (
              <D.Description className="sr-only">{title}</D.Description>
            )}
          </div>
          <D.Close className="rounded-md p-1 text-muted hover:bg-sunken" aria-label="Close">
            <X className="size-4" />
          </D.Close>
        </div>
        {children}
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
      <DM.Content
        sideOffset={6}
        align="end"
        className={cn("z-50 min-w-60 rounded-lg border border-line bg-surface p-1 shadow-pop", className)}
        {...props}
      />
    </DM.Portal>
  );
}

export function MenuItem({ className, ...props }: React.ComponentProps<typeof DM.Item>) {
  return (
    <DM.Item
      className={cn(
        "flex cursor-default items-center gap-2 rounded-md px-2 py-1.5 text-[13px] text-ink outline-none select-none data-[disabled]:opacity-45 data-[highlighted]:bg-sunken [&_svg]:size-4 [&_svg]:text-muted",
        className,
      )}
      {...props}
    />
  );
}

export function MenuLabel({ className, ...props }: React.ComponentProps<typeof DM.Label>) {
  return (
    <DM.Label
      className={cn("px-2 pt-1.5 pb-1 text-[11px] font-medium tracking-wide text-faint uppercase", className)}
      {...props}
    />
  );
}

export function MenuSeparator() {
  return <DM.Separator className="my-1 h-px bg-line" />;
}

/* Popover ------------------------------------------------------------------------------ */
export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;

export function PopoverContent({ className, ...props }: React.ComponentProps<typeof P.Content>) {
  return (
    <P.Portal>
      <P.Content
        sideOffset={6}
        collisionPadding={12}
        className={cn(
          "z-50 w-72 rounded-lg border border-line bg-surface p-3 shadow-pop focus:outline-none",
          className,
        )}
        {...props}
      />
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
          sideOffset={5}
          collisionPadding={8}
          className="z-50 max-w-72 rounded-md bg-ink px-2 py-1 text-[12px] leading-snug text-paper shadow-pop"
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
