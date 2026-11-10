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
      <div className="grid grid-cols-[64px_1fr] gap-x-3">
        {/* ruler */}
        <div />
        <div className="relative h-5 text-[10px] text-faint tabular" aria-hidden>
          {model.ticks.map((t, i) => (
            <span key={t} className={cn("absolute top-0 -translate-x-1/2", i % 2 === 1 && "max-sm:hidden")} style={{ left: pct(t) }}>
              {axisLabel(t)}
            </span>
          ))}
        </div>

        {/* lanes */}
        <div className="flex flex-col" aria-hidden>
          {model.lanes.map(({ lane, rows }) => (
            <div
              key={lane}
              className="flex items-center text-[11px] font-medium text-muted"
              style={{ height: rows * (ROW + ROW_GAP) + 6 }}
            >
              {LANE_LABELS[lane]}
            </div>
          ))}
        </div>
        <div className="relative overflow-hidden rounded-md border border-line bg-[#efebe4]">
          {/* clip, hook and ending windows */}
          <div className="absolute inset-y-0 bg-surface" style={{ left: pct(model.clip[0]), width: width(...model.clip) }} />
          <div
            className="absolute inset-y-0 bg-amber-soft/70"
            style={{ left: pct(model.hook[0]), width: width(...model.hook) }}
          />
          <div
            className="absolute inset-y-0 bg-amber-soft/70"
            style={{ left: pct(model.ending[0]), width: width(...model.ending) }}
          />
          <div className="absolute inset-y-0 w-px bg-amber" style={{ left: pct(model.clip[0]) }} />
          <div className="absolute inset-y-0 w-px bg-amber" style={{ left: pct(model.clip[1]) }} />
          {model.ticks.map((t) => (
            <div key={t} className="absolute inset-y-0 w-px bg-line/70" style={{ left: pct(t) }} aria-hidden />
          ))}

          <div className="relative">
            {model.lanes.map(({ lane, rows, events }) => (
              <div
                key={lane}
                role="group"
                aria-label={`${LANE_LABELS[lane]} events`}
                className="relative border-b border-line/60 last:border-b-0"
                style={{ height: rows * (ROW + ROW_GAP) + 6 }}
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

        {/* window legend */}
        <div />
        <div className="relative mt-1.5 h-4 text-[10.5px] font-medium tabular" aria-hidden>
          <span className="absolute whitespace-nowrap text-amber-ink" style={{ left: pct(model.clip[0]) }}>
            ▲ {timecode(model.clip[0])}
          </span>
          <span className="absolute -translate-x-full whitespace-nowrap text-amber-ink" style={{ left: pct(model.clip[1]) }}>
            {timecode(model.clip[1])} ▲
          </span>
        </div>
      </div>
      <figcaption className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11.5px] text-muted sm:pl-[76px]">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-[2px] border border-amber bg-surface" /> Clip
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-[2px] bg-amber-soft" /> Hook (first 3 s) · Ending (last 5 s)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-[2px] bg-ev-dialogue ring-2 ring-amber ring-offset-1" /> AI evidence
        </span>
        <span>Context ±10 s · screenplay timing, not video</span>
      </figcaption>
    </figure>
  );
});

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
        isEvidence && "ring-2 ring-amber ring-offset-1 ring-offset-surface",
        active && "outline-2 outline-ink outline-offset-1",
      )}
      style={{
        left,
        // a 1px gap keeps back-to-back shots visibly separate
        width: instant ? 3 : `max(3px, calc(${width} - 1px))`,
        top: 3 + placed.row * (ROW + ROW_GAP),
        height: ROW,
      }}
    />
  );
});
