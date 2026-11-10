import { ChevronLeft, ChevronRight } from "lucide-react";
import * as React from "react";
import type { DocEvent } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import type { Lane, PlacedEvent, TimelineModel } from "@/lib/doc-index";
import { LANE_LABELS } from "@/lib/labels";
import { axisLabel, seconds, timecode } from "@/lib/time";
import { cn } from "@/lib/utils";

/* Screenplay map: a clip band on top, one ribbon of event chips per category, a time axis
   below. Positions come straight from the model's millisecond timings (unchanged maths). */

const CHIP = 10; // chip height
const ROW = 16; // chip + gap, per overlap row
const LANE_PAD = 5;

export const LANE_TONE: Record<Lane, string> = {
  DIALOGUE: "bg-ev-dialogue",
  ACTION: "bg-ev-action",
  SOUND_FX: "bg-ev-sound",
  CAMERA: "bg-ev-camera",
};

type Evidence = { hook: Set<string>; cliffhanger: Set<string> };

export interface TimelineProps {
  model: TimelineModel;
  evidence: Evidence;
  activeId: string | null;
  onActivate: (id: string) => void;
}

/** A screenplay timeline: real event timings around the clip. Not a video player. */
export const Timeline = React.memo(function Timeline({ model, evidence, activeId, onActivate }: TimelineProps) {
  const { lo, hi } = model;
  const span = Math.max(1, hi - lo);
  const frac = (t: number) => (Math.min(Math.max(t, lo), hi) - lo) / span;
  const pct = (t: number) => `${frac(t) * 100}%`;
  const width = (a: number, b: number) => `${(Math.max(0, Math.min(b, hi) - Math.max(a, lo)) / span) * 100}%`;
  const [clipStart, clipEnd] = model.clip;
  const sceneCuts = model.scenes.map((s) => s.start_ms).filter((t) => t > lo && t < hi);
  const active = activeId ? model.events.find((p) => p.event.id === activeId) : undefined;

  // Arrow keys step through events in screenplay order while a chip has focus.
  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    const id = (e.target as HTMLElement).dataset.eventId;
    if (!step || !id) return;
    const i = model.events.findIndex((p) => p.event.id === id);
    const next = model.events[i + step];
    if (!next) return;
    e.preventDefault();
    onActivate(next.event.id);
    (e.currentTarget.querySelector(`[data-event-id="${next.event.id}"]`) as HTMLElement | null)?.focus();
  };

  const overlays = (
    <>
      {/* context outside the clip is shaded; the clip stays clear */}
      <div className="absolute inset-y-0 left-0 bg-canvas" style={{ width: pct(clipStart) }} />
      <div className="absolute inset-y-0 right-0 bg-canvas" style={{ left: pct(clipEnd) }} />
      <div className="absolute inset-y-0 bg-accent-wash" style={{ left: pct(model.hook[0]), width: width(...model.hook) }} />
      <div className="absolute inset-y-0 bg-accent-wash" style={{ left: pct(model.ending[0]), width: width(...model.ending) }} />
      {model.ticks.map((t) => (
        <div key={t} className="absolute inset-y-0 w-px bg-line/70" style={{ left: pct(t) }} />
      ))}
      {sceneCuts.map((t) => (
        <div key={`s${t}`} className="absolute inset-y-0 border-l border-dashed border-line-strong" style={{ left: pct(t) }} />
      ))}
      <div className="absolute inset-y-0 w-px bg-accent/50" style={{ left: pct(clipStart) }} />
      <div className="absolute inset-y-0 w-px bg-accent/50" style={{ left: pct(clipEnd) }} />
    </>
  );

  return (
    <figure aria-label="Screenplay timeline around the clip" className="select-none" onKeyDown={onKeyDown}>
      <div className="grid grid-cols-[80px_1fr] sm:grid-cols-[88px_1fr]">
        {/* clip band */}
        <div className="flex h-8 items-center text-[11px] font-bold tracking-[0.04em] text-muted uppercase">Clip</div>
        <div className="relative h-8" aria-hidden>
          <div className="absolute inset-x-0 top-1/2 h-[3px] -translate-y-1/2 rounded-full bg-subtle" />
          <div
            className="absolute inset-y-1 flex items-center justify-between overflow-hidden rounded-md bg-accent-soft px-2 ring-1 ring-accent/35 ring-inset"
            style={{ left: pct(clipStart), width: width(clipStart, clipEnd) }}
          >
            <span className="absolute inset-y-0 left-0 bg-accent/20" style={{ width: `${((model.hook[1] - model.hook[0]) / Math.max(1, clipEnd - clipStart)) * 100}%` }} />
            <span className="absolute inset-y-0 right-0 bg-accent/20" style={{ width: `${((model.ending[1] - model.ending[0]) / Math.max(1, clipEnd - clipStart)) * 100}%` }} />
            <span className="relative text-[11px] font-bold whitespace-nowrap text-accent-hover tabular">
              <span className="mr-1 font-semibold text-accent/80 max-sm:hidden">In</span>
              {timecode(clipStart)}
            </span>
            <span className="relative mx-2 truncate text-[11px] font-semibold text-accent/80 tabular max-md:hidden">{seconds(clipEnd - clipStart)}</span>
            <span className="relative text-[11px] font-bold whitespace-nowrap text-accent-hover tabular">
              <span className="mr-1 font-semibold text-accent/80 max-sm:hidden">Out</span>
              {timecode(clipEnd)}
            </span>
          </div>
        </div>

        {/* lane labels */}
        <div className="mt-1.5 flex flex-col" aria-hidden>
          {model.lanes.map(({ lane, rows }) => (
            <div key={lane} className="flex items-start gap-2 text-[12px] font-semibold text-ink-soft" style={{ height: rows * ROW + LANE_PAD * 2 - (ROW - CHIP) }}>
              <span className="flex items-center gap-2" style={{ height: CHIP + LANE_PAD * 2 }}>
                <span className={cn("size-2 rounded-full", LANE_TONE[lane])} />
                {LANE_LABELS[lane]}
              </span>
            </div>
          ))}
        </div>

        {/* event ribbons */}
        <div className="relative mt-1.5 rounded-md ring-1 ring-line">
          {/* clipped in their own layer so evidence rings on edge chips stay visible */}
          <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-md">{overlays}</div>
          <div className="relative">
            {model.lanes.map(({ lane, rows, events }) => (
              <div
                key={lane}
                role="group"
                aria-label={`${LANE_LABELS[lane]} events`}
                className="relative"
                style={{ height: rows * ROW + LANE_PAD * 2 - (ROW - CHIP) }}
              >
                {events.map((p) => (
                  <EventChip
                    key={p.event.id}
                    placed={p}
                    lane={lane}
                    left={pct(p.event.start_ms)}
                    width={width(p.event.start_ms, p.event.end_ms)}
                    active={activeId === p.event.id}
                    isEvidence={evidence.hook.has(p.event.id) || evidence.cliffhanger.has(p.event.id)}
                    onActivate={onActivate}
                  />
                ))}
              </div>
            ))}
          </div>
        </div>

        {/* time axis */}
        <div />
        <div className="relative mt-1.5 h-5 text-[11px] font-medium text-muted tabular" aria-hidden>
          {model.ticks.map((t, i) => (
            <span key={t} className={cn("absolute top-0.5 -translate-x-1/2", i % 2 === 1 && "max-sm:hidden", active && "opacity-40")} style={{ left: pct(t) }}>
              {axisLabel(t)}
            </span>
          ))}
          {active && (
            <span
              className="absolute top-0 z-[1] rounded-[4px] bg-ink px-1.5 py-px text-[11px] font-bold whitespace-nowrap text-white"
              style={frac(active.event.start_ms) > 0.6 ? { right: `${(1 - frac(active.event.end_ms)) * 100}%` } : { left: pct(active.event.start_ms) }}
            >
              {timecode(active.event.start_ms)}
              {active.event.end_ms !== active.event.start_ms && ` – ${timecode(active.event.end_ms)}`}
            </span>
          )}
        </div>
      </div>
      <figcaption className="sr-only">
        Clip from {timecode(clipStart)} to {timecode(clipEnd)}, with the first 3 seconds and last 5 seconds marked, and up
        to 10 seconds of context on each side. Screenplay timing, not video.
      </figcaption>
    </figure>
  );
});

/** Compact legend shown beside the timeline heading. Categories are labelled on the lanes. */
export function TimelineLegend({ evidence = true }: { evidence?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] font-medium text-muted" aria-hidden>
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-4 rounded-[3px] bg-accent/25 ring-1 ring-accent/35" /> First 3 s · last 5 s
      </span>
      {evidence && (
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-4 rounded-full bg-ev-dialogue ring-2 ring-accent ring-offset-1" /> AI evidence
        </span>
      )}
      <span className="flex items-center gap-1.5 max-sm:hidden">
        <span className="h-3 border-l border-dashed border-line-strong" /> Scene change
      </span>
      <span className="max-sm:hidden">±10 s context</span>
    </div>
  );
}

function describe(ev: DocEvent): string {
  const who = ev.speaker ? `${ev.speaker}: ` : "";
  return `${LANE_LABELS[ev.type as Lane] ?? ev.type} ${timecode(ev.start_ms)} to ${timecode(ev.end_ms)}. ${who}${ev.text}`;
}

const EventChip = React.memo(function EventChip({
  placed,
  lane,
  left,
  width,
  active,
  isEvidence,
  onActivate,
}: {
  placed: PlacedEvent;
  lane: Lane;
  left: string;
  width: string;
  active: boolean;
  isEvidence: boolean;
  onActivate: (id: string) => void;
}) {
  const instant = placed.event.start_ms === placed.event.end_ms;
  return (
    <button
      type="button"
      aria-label={describe(placed.event)}
      aria-pressed={active}
      data-event-id={placed.event.id}
      onClick={() => onActivate(placed.event.id)}
      onFocus={() => onActivate(placed.event.id)}
      className={cn(
        "absolute rounded-full transition-[opacity,box-shadow,transform] hover:opacity-100 focus-visible:outline-offset-2",
        LANE_TONE[lane],
        placed.inClip ? "opacity-100" : "opacity-30 hover:opacity-70",
        isEvidence && "ring-2 ring-accent ring-offset-1 ring-offset-surface",
        active && "z-[1] scale-y-125 outline-2 outline-offset-2 outline-ink",
      )}
      style={{
        left,
        // a 2px gap keeps back-to-back events visibly separate
        width: instant ? CHIP : `max(6px, calc(${width} - 2px))`,
        marginLeft: instant ? -CHIP / 2 : undefined,
        top: LANE_PAD + placed.row * ROW,
        height: CHIP,
      }}
    />
  );
});

/* Event card under the map ------------------------------------------------------------ */

export function EventInspector({
  id,
  model,
  evidence,
  onActivate,
}: {
  id: string | null;
  model: TimelineModel;
  evidence: Evidence;
  onActivate: (id: string) => void;
}) {
  const index = id ? model.events.findIndex((p) => p.event.id === id) : -1;
  const placed = index >= 0 ? model.events[index] : undefined;
  const firstInClip = Math.max(0, model.events.findIndex((p) => p.inClip));
  const go = (step: number) => {
    const target = index < 0 ? model.events[firstInClip] : model.events[index + step];
    if (target) onActivate(target.event.id);
  };
  const stepper = (
    <div className="flex shrink-0 items-center gap-0.5">
      <button
        type="button"
        aria-label="Previous event"
        onClick={() => go(-1)}
        disabled={index <= 0}
        className="rounded-md p-1 text-muted hover:bg-subtle hover:text-ink disabled:opacity-35 disabled:hover:bg-transparent"
      >
        <ChevronLeft className="size-4" />
      </button>
      <button
        type="button"
        aria-label="Next event"
        onClick={() => go(1)}
        disabled={index >= model.events.length - 1}
        className="rounded-md p-1 text-muted hover:bg-subtle hover:text-ink disabled:opacity-35 disabled:hover:bg-transparent"
      >
        <ChevronRight className="size-4" />
      </button>
    </div>
  );

  if (!placed) {
    return (
      <div className="mt-4 flex items-center justify-between gap-3 rounded-lg border border-dashed border-line-strong px-4 py-3">
        <p className="text-[13px] text-muted">Select an event to read it here, or step through events with the arrows.</p>
        {stepper}
      </div>
    );
  }
  const ev = placed.event;
  const where = placed.inClip ? "In clip" : ev.start_ms < model.clip[0] ? "Before clip" : "After clip";
  const tags = [
    placed.inHook && "First 3 s",
    placed.inEnding && "Last 5 s",
    evidence.hook.has(ev.id) && "AI hook evidence",
    evidence.cliffhanger.has(ev.id) && "AI cliffhanger evidence",
  ].filter(Boolean) as string[];
  return (
    <div className="mt-4 rounded-lg border border-line" aria-live="polite">
      <div className="flex items-center gap-3 border-b border-line py-2 pr-2 pl-4">
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-1 text-[12px]">
          <span className="flex items-center gap-1.5 font-bold text-ink">
            <span className={cn("size-2 rounded-full", LANE_TONE[ev.type as Lane])} />
            {LANE_LABELS[ev.type as Lane] ?? ev.type}
          </span>
          <span className="font-semibold text-ink-soft tabular">
            {timecode(ev.start_ms)} – {timecode(ev.end_ms)}
          </span>
          <span className="text-muted tabular">{seconds(ev.end_ms - ev.start_ms)}</span>
          <Badge tone={placed.inClip ? "accent" : "outline"}>{where}</Badge>
          {tags.map((t) => (
            <Badge key={t} tone={t.startsWith("AI") ? "solid" : "neutral"}>{t}</Badge>
          ))}
        </div>
        {stepper}
      </div>
      <div className="px-4 py-3">
        <p className="text-[14px] leading-relaxed text-ink">
          {ev.speaker && (
            <span className="font-bold">
              {ev.speaker}
              {ev.annotations.length ? <span className="font-medium text-muted"> ({ev.annotations.join(", ")})</span> : null}:{" "}
            </span>
          )}
          <span className={cn(ev.type === "ACTION" && "text-ink-soft italic", ev.type === "CAMERA" && "text-ink-soft")}>{ev.text}</span>
        </p>
        <p className="mt-1.5 font-mono text-[11px] text-muted">{ev.id}</p>
      </div>
    </div>
  );
}
