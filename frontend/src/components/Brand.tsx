import { cn } from "@/lib/utils";

/* FrameCue mark: a split frame (the shot) with a playhead cue through it. Drawn on a 32-unit
   grid with 2.6-unit strokes so it stays legible from 16 px up. `duo` puts the playhead in the
   accent colour; `mono` uses currentColor for everything. Keep in sync with public/favicon.svg. */
const FRAME =
  "M12 5.5H9.5a4.5 4.5 0 0 0-4.5 4.5v12a4.5 4.5 0 0 0 4.5 4.5H12M20 5.5h2.5a4.5 4.5 0 0 1 4.5 4.5v12a4.5 4.5 0 0 1-4.5 4.5H20";
const HEAD = "M12.7 4h6.6v3.6L16 10.6l-3.3-3z";

export function BrandMark({ className, tone = "duo" }: { className?: string; tone?: "duo" | "mono" }) {
  const cue = tone === "duo" ? "var(--color-accent)" : "currentColor";
  return (
    <svg viewBox="0 0 32 32" aria-hidden className={cn("size-6 shrink-0 text-ink", className)}>
      <path d={FRAME} fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d={HEAD} fill={cue} stroke={cue} strokeWidth="1.3" strokeLinejoin="round" />
      <rect x="14.75" y="9" width="2.5" height="17.5" rx="1.25" fill={cue} />
    </svg>
  );
}

/** Compact standalone icon: the mark reversed out of an accent tile (also the favicon). */
export function BrandIcon({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex size-8 shrink-0 items-center justify-center rounded-[22%] bg-accent", className)} aria-hidden>
      <BrandMark tone="mono" className="size-[78%] text-white" />
    </span>
  );
}

/** Primary lockup: mark plus wordmark. */
export function Brand({ size = "md", className }: { size?: "sm" | "md"; className?: string }) {
  return (
    <span className={cn("inline-flex items-center", size === "sm" ? "gap-1.5" : "gap-2", className)}>
      <BrandMark className={size === "sm" ? "size-[22px]" : "size-[26px]"} />
      <span className={cn("font-bold tracking-[-0.02em] text-ink", size === "sm" ? "text-[15px]" : "text-[17px]")}>FrameCue</span>
    </span>
  );
}
