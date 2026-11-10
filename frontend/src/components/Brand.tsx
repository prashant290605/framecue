import { cn } from "@/lib/utils";

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-7", className)}>
      <rect width="32" height="32" rx="8" fill="var(--color-ink)" />
      <circle cx="16" cy="16" r="8.5" fill="none" stroke="var(--color-paper)" strokeWidth="2.2" />
      <circle cx="16" cy="16" r="3.2" fill="var(--color-amber)" />
    </svg>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <BrandMark className={compact ? "size-6" : "size-7"} />
      <span className={cn("font-display font-semibold tracking-[-0.01em] text-ink", compact ? "text-[16px]" : "text-[18px]")}>
        PromoLens
      </span>
    </span>
  );
}
