import * as React from "react";
import type { DocEvent } from "@/api/types";
import type { Lane, PlacedEvent, TimelineModel } from "@/lib/doc-index";
import { LANE_LABELS } from "@/lib/labels";
import { axisLabel, timecode } from "@/lib/time";
import { cn } from "@/lib/utils";

const ROW = 12; // px per sub-row
const ROW_GAP = 3;

const LANE_BAR: Record<Lane, string> = {
  DIALOGUE: "bg-ev-dialogue",
  ACTION: "bg-ev-action",
  SOUND_FX: "bg-ev-sound",
  CAMERA: "bg-ev-camera",
};

export interface TimelineProps {
  model: TimelineModel;
  evidence: { hook: Set<string>; cliffhanger: Set<string> };
  activeId: string | null;
  onActivate: (id: string) => void;
}

/** A screenplay timeline: real event timings around the clip. Not a video player. */
export const Timeline = React.memo(function Timeline({ model, evidence, activeId, onActivate }: TimelineProps) {
  const { lo, hi } = model;
  const span = Math.max(1, hi - lo);
  const pct = (t: number) => `${((Math.min(Math.max(t, lo), hi) - lo) / span) * 100}%`;
  const width = (a: number, b: number) => `${(Math.max(0, Math.min(b, hi) - Math.max(a, lo)) / span) * 100}%`;

  return (
    <figure aria-label="Screenplay timeline around the clip" className="select-none">
      <div className="grid grid-cols-[60px_1fr] gap-x-3 sm:grid-cols-[72px_1fr]">
        {/* ruler */}
        <div />
        <div className="relative mb-1.5 h-4 text-[11px] font-medium text-muted tabular" aria-hidden>
          {model.ticks.map((t, i) => (
            <span key={t} className={cn("absolute top-0 -translate-x-1/2", i % 2 === 1 && "max-sm:hidden")} style={{ left: pct(t) }}>
              {axisLabel(t)}
            </span>
          ))}
        </div>

        {/* lane labels */}
        <div className="flex flex-col" aria-hidden>
          {model.lanes.map(({ lane, rows }) => (
            <div key={lane} className="flex items-center text-[12px] font-semibold text-ink-soft" style={{ height: rows * (ROW + ROW_GAP) + 7 }}>
              {LANE_LABELS[lane]}
            </div>
          ))}
        </div>

        {/* track */}
        <div className="relative overflow-hidden rounded-md bg-canvas ring-1 ring-line">
          {model.ticks.map((t) => (
            <div key={t} className="absolute inset-y-0 w-px bg-line" style={{ left: pct(t) }} aria-hidden />
          ))}
          {/* clip, hook and ending windows */}
          <div className="absolute inset-y-0 bg-surface" style={{ left: pct(model.clip[0]), width: width(...model.clip) }} />
          <div className="absolute inset-y-0 bg-accent-soft/80" style={{ left: pct(model.hook[0]), width: width(...model.hook) }} />
          <div className="absolute inset-y-0 bg-accent-soft/80" style={{ left: pct(model.ending[0]), width: width(...model.ending) }} />
          <div className="absolute inset-y-0 w-[2px] -translate-x-1/2 bg-accent" style={{ left: pct(model.clip[0]) }} />
          <div className="absolute inset-y-0 w-[2px] -translate-x-1/2 bg-accent" style={{ left: pct(model.clip[1]) }} />

          <div className="relative">
            {model.lanes.map(({ lane, rows, events }) => (
              <div
                key={lane}
                role="group"
                aria-label={`${LANE_LABELS[lane]} events`}
                className="relative border-b border-line/70 last:border-b-0"
                style={{ height: rows * (ROW + ROW_GAP) + 7 }}
              >
                {events.map((p) => (
                  <EventBar
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

        {/* clip in and out points */}
        <div />
        <div className="relative mt-2 h-5 text-[11px] font-bold tabular" aria-hidden>
          <span className="absolute flex h-5 items-center rounded-[4px] bg-accent px-1.5 whitespace-nowrap text-white" style={{ left: pct(model.clip[0]) }}>
            <span className="mr-1 max-sm:hidden">In</span>{timecode(model.clip[0])}
          </span>
          <span
            className="absolute flex h-5 -translate-x-full items-center rounded-[4px] bg-accent px-1.5 whitespace-nowrap text-white"
            style={{ left: pct(model.clip[1]) }}
          >
            <span className="mr-1 max-sm:hidden">Out</span>{timecode(model.clip[1])}
          </span>
        </div>
      </div>
      <figcaption className="sr-only">
        Clip from {timecode(model.clip[0])} to {timecode(model.clip[1])}, with the first 3 seconds and last 5 seconds marked, and
        10 seconds of context on each side. Screenplay timing, not video.
      </figcaption>
    </figure>
  );
});

/** Compact legend shown beside the timeline heading. */
export function TimelineLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] font-medium text-muted" aria-hidden>
      <span className="flex items-center gap-1.5">
        <span className="h-3 w-4 rounded-[3px] bg-surface ring-1 ring-accent" /> Clip
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-3 w-4 rounded-[3px] bg-accent-soft" /> First 3 s · last 5 s
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-2 w-4 rounded-[2px] bg-ev-dialogue ring-2 ring-accent ring-offset-1" /> AI evidence
      </span>
      <span className="max-sm:hidden">±10 s context</span>
    </div>
  );
}

function describe(ev: DocEvent): string {
  const who = ev.speaker ? `${ev.speaker}: ` : "";
  return `${LANE_LABELS[ev.type as Lane] ?? ev.type} ${timecode(ev.start_ms)} to ${timecode(ev.end_ms)}. ${who}${ev.text}`;
}

const EventBar = React.memo(function EventBar({
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
        "absolute rounded-[3px] transition-[opacity,box-shadow] focus-visible:outline-offset-1",
        LANE_BAR[lane],
        placed.inClip ? "opacity-100" : "opacity-35 hover:opacity-70",
        isEvidence && "ring-2 ring-accent ring-offset-1 ring-offset-surface",
        active && "outline-2 outline-offset-1 outline-ink",
      )}
      style={{
        left,
        // a 1px gap keeps back-to-back shots visibly separate
        width: instant ? 3 : `max(3px, calc(${width} - 1px))`,
        top: 3.5 + placed.row * (ROW + ROW_GAP),
        height: ROW,
      }}
    />
  );
});
