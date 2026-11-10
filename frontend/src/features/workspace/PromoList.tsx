import { useVirtualizer } from "@tanstack/react-virtual";
import { Check, Clock3, CornerDownRight, Sparkles, X } from "lucide-react";
import * as React from "react";
import type { ViewName } from "@/api/types";
import type { DocIndex, Entry, Reviews } from "@/lib/doc-index";
import { seconds, timecode } from "@/lib/time";
import { cn } from "@/lib/utils";

export const ROW_HEIGHT = 76;

interface Props {
  index: DocIndex;
  view: ViewName;
  entries: Entry[];
  reviews: Reviews;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function PromoList({ index, view, entries, reviews, selectedId, onSelect }: Props) {
  const parent = React.useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => parent.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 8,
    initialRect: { width: 400, height: 800 }, // lets tests and first paint render rows
  });
  const selectedIndex = entries.findIndex((e) => e.id === selectedId);

  React.useEffect(() => {
    if (selectedIndex >= 0) virtualizer.scrollToIndex(selectedIndex, { align: "auto" });
  }, [selectedIndex, virtualizer]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = e.key === "ArrowDown" || e.key === "j" ? 1 : e.key === "ArrowUp" || e.key === "k" ? -1 : 0;
    if (!step || entries.length === 0) return;
    e.preventDefault();
    const next = Math.min(entries.length - 1, Math.max(0, (selectedIndex < 0 ? -1 : selectedIndex) + step));
    onSelect(entries[next].id);
  };

  return (
    <div
      ref={parent}
      role="listbox"
      tabIndex={0}
      aria-label={view === "ai" ? "AI-reviewed recommendations" : "Full-show candidates"}
      aria-activedescendant={selectedId ? `row-${selectedId}` : undefined}
      onKeyDown={onKeyDown}
      className="h-full overflow-y-auto overscroll-contain focus-visible:outline-none"
    >
      <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
        {virtualizer.getVirtualItems().map((item) => {
          const entry = entries[item.index];
          return (
            <div
              key={entry.id}
              className="absolute inset-x-0"
              style={{ height: ROW_HEIGHT, transform: `translateY(${item.start}px)` }}
            >
              <Row
                index={index}
                view={view}
                entry={entry}
                decision={reviews[entry.id]?.decision ?? null}
                hasNote={Boolean(reviews[entry.id]?.note)}
                selected={entry.id === selectedId}
                onSelect={onSelect}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

const Row = React.memo(function Row({
  index,
  view,
  entry,
  decision,
  hasNote,
  selected,
  onSelect,
}: {
  index: DocIndex;
  view: ViewName;
  entry: Entry;
  decision: string | null;
  hasNote: boolean;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const c = index.candidates.get(entry.id)!;
  const ai = index.ai[entry.id];
  const preview = view === "ai" && ai ? ai.why_it_works : openingLine(index, entry.id);
  return (
    <div
      id={`row-${entry.id}`}
      role="option"
      aria-selected={selected}
      onClick={() => onSelect(entry.id)}
      className={cn(
        "group mx-2 flex h-[70px] cursor-pointer gap-3 rounded-md border px-3 py-2.5 transition-colors",
        selected ? "border-amber/60 bg-surface shadow-card" : "border-transparent hover:bg-surface/70",
      )}
    >
      <div className="w-8 shrink-0 pt-px text-right">
        {entry.rank !== null ? (
          <span className="text-[12.5px] font-semibold text-ink-soft tabular">{entry.rank}</span>
        ) : (
          <CornerDownRight className="ml-auto size-3.5 text-faint" aria-label="Alternative cut" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[12.5px]">
          <span className="font-medium text-ink">Ep {c.episode_number}</span>
          <span className="font-mono text-[12px] text-ink-soft tabular">
            {timecode(c.start_ms)}–{timecode(c.end_ms)}
          </span>
          <span className="text-[12px] text-faint tabular">{seconds(c.duration_ms)}</span>
        </div>
        <p className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-muted">{preview}</p>
      </div>
      <div className="flex w-14 shrink-0 flex-col items-end justify-between">
        {view === "ai" && ai ? (
          <span className="text-right">
            <span className="block text-[15px] leading-none font-semibold text-ink tabular">{ai.weighted_score}</span>
            <span className="text-[10.5px] text-faint tabular">H{ai.hook_score} C{ai.cliffhanger_score}</span>
          </span>
        ) : (
          <span className="text-right">
            <span className="block text-[13px] leading-none font-medium text-ink-soft tabular">{c.baseline.priority.toFixed(3)}</span>
            <span className="text-[10.5px] text-faint">priority</span>
          </span>
        )}
        <span className="flex items-center gap-1">
          {view === "coverage" && ai && (
            <Sparkles className="size-3.5 text-amber-ink" aria-label="AI review available" />
          )}
          {hasNote && <span className="size-1.5 rounded-full bg-faint" aria-label="Has a note" />}
          {decision === "accepted" && <Check className="size-3.5 text-ok" aria-label="Accepted" />}
          {decision === "rejected" && <X className="size-3.5 text-no" aria-label="Rejected" />}
          {decision === "later" && <Clock3 className="size-3.5 text-amber-ink" aria-label="Marked for later" />}
        </span>
      </div>
    </div>
  );
});

const openingCache = new WeakMap<DocIndex, Map<string, string>>();

/** First line of dialogue (or first event) inside the clip's opening window. */
function openingLine(index: DocIndex, id: string): string {
  let cache = openingCache.get(index);
  if (!cache) {
    cache = new Map();
    openingCache.set(index, cache);
  }
  const hit = cache.get(id);
  if (hit !== undefined) return hit;
  const c = index.candidates.get(id)!;
  const events = c.source_events.opening_window.map((e) => index.eventById.get(e)).filter(Boolean);
  const line = events.find((e) => e!.type === "DIALOGUE") ?? events.find((e) => e!.type !== "CAMERA") ?? events[0];
  const text = line ? `${line.speaker ? `${line.speaker}: ` : ""}${line.text}` : "";
  cache.set(id, text);
  return text;
}
