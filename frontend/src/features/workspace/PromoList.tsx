import { useVirtualizer } from "@tanstack/react-virtual";
import { BadgeCheck, CircleCheck, CircleX, Clock3, CornerDownRight, StickyNote } from "lucide-react";
import * as React from "react";
import type { ViewName } from "@/api/types";
import type { DocIndex, Entry, Reviews } from "@/lib/doc-index";
import { timecode } from "@/lib/time";
import { cn } from "@/lib/utils";

export const ROW_HEIGHT = 66;

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
      className="h-full overflow-y-auto overscroll-contain pb-3 focus-visible:outline-none"
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
        "group relative mx-2 flex h-[60px] cursor-pointer gap-3 rounded-lg px-3 py-2.5 transition-colors",
        selected ? "bg-surface shadow-[0_1px_2px_rgb(20_34_56/0.06)] ring-1 ring-line" : "hover:bg-subtle",
      )}
    >
      {selected && <span className="absolute top-2.5 bottom-2.5 left-0 w-[3px] rounded-full bg-accent" aria-hidden />}
      <div className="w-6 shrink-0 pt-[3px] text-right">
        {entry.rank !== null ? (
          <span className={cn("text-[12px] font-semibold tabular", selected ? "text-accent" : "text-muted")}>{entry.rank}</span>
        ) : (
          <CornerDownRight className="ml-auto size-3.5 text-faint" aria-label="Alternative cut" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 whitespace-nowrap">
          <span
            className={cn(
              "inline-flex h-5 items-center rounded-[5px] px-1.5 text-[11px] font-bold tabular",
              selected ? "bg-accent-soft text-accent-hover" : "bg-subtle text-ink-soft group-hover:bg-surface",
            )}
          >
            EP {c.episode_number}
          </span>
          <span className="text-[13px] font-semibold text-ink tabular">
            {timecode(c.start_ms)} – {timecode(c.end_ms)}
          </span>
          <span className="text-[12px] text-muted tabular">{shortSeconds(c.duration_ms)}</span>
        </div>
        <p className="mt-1 truncate text-[12px] leading-[1.4] text-muted">{preview}</p>
      </div>
      <div className="flex w-12 shrink-0 flex-col items-end justify-between">
        {view === "ai" && ai ? (
          <span className="text-right" title="AI score out of 100">
            <span className="block text-[15px] leading-none font-bold text-ink tabular">{ai.weighted_score}</span>
            <span className="mt-1 block text-[10.5px] font-medium text-muted">AI</span>
          </span>
        ) : (
          <span className="text-right" title="Structural score, 0 to 1">
            <span className="block text-[13px] leading-none font-semibold text-ink-soft tabular">{c.baseline.priority.toFixed(2)}</span>
          </span>
        )}
        <span className="flex items-center gap-1.5 [&_svg]:size-3.5">
          {view === "coverage" && ai && <BadgeCheck className="text-accent" aria-label="AI review available" />}
          {hasNote && <StickyNote className="text-muted" aria-label="Has a note" />}
          {decision === "accepted" && <CircleCheck className="text-ok" aria-label="Accepted" />}
          {decision === "rejected" && <CircleX className="text-no" aria-label="Rejected" />}
          {decision === "later" && <Clock3 className="text-later" aria-label="Marked for later" />}
        </span>
      </div>
    </div>
  );
});

/** "37.4 s" for list rows; the inspector shows centisecond precision. */
function shortSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

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
