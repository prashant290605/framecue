import { m, useReducedMotion } from "motion/react";
import {
  ArrowLeft,
  BadgeCheck,
  ChevronRight,
  CircleCheck,
  CircleX,
  Clock3,
  Copy,
  MousePointerClick,
  StickyNote,
} from "lucide-react";
import * as React from "react";
import type { AIJudgement, DocCandidate, DocEvent, ViewName } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Tip } from "@/components/ui/overlays";
import { buildTimeline, cutFamily, type Decision, type DocIndex } from "@/lib/doc-index";
import { anchorLabel, COMPONENT_LABELS, RUBRIC, RUBRIC_WEIGHT_KEY, warningLabel } from "@/lib/labels";
import { seconds, timecode } from "@/lib/time";
import { cn } from "@/lib/utils";
import { useApp } from "@/state/app-state";
import { EventInspector, Timeline, TimelineLegend } from "./Timeline";

type Model = ReturnType<typeof buildTimeline>;
type Evidence = { hook: Set<string>; cliffhanger: Set<string> };

export function Detail({ onBack }: { onBack?: () => void }) {
  const { state } = useApp();
  const { index, selectedId, view } = state;
  const reduce = useReducedMotion();
  if (!index) return null;
  const c = selectedId ? index.candidates.get(selectedId) : undefined;
  if (!c) return <NoSelection />;
  // Keyed by candidate: the new cut renders immediately (no exit wait), with a short fade-in.
  return (
    <m.article
      key={c.candidate_id}
      initial={reduce ? false : { opacity: 0, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.14, ease: "easeOut" }}
      className="mx-auto w-full max-w-[920px] px-5 pt-5 pb-20 sm:px-8 sm:pt-7 xl:px-10"
      aria-label={`Candidate ${c.candidate_id}`}
    >
      <DetailBody index={index} c={c} view={view} onBack={onBack} />
    </m.article>
  );
}

function NoSelection() {
  return (
    <div className="flex h-full flex-col items-center justify-center p-10 text-center">
      <span className="flex size-10 items-center justify-center rounded-full bg-canvas text-muted ring-1 ring-line">
        <MousePointerClick className="size-[18px]" />
      </span>
      <p className="mt-4 text-[14px] font-semibold text-ink">No cut selected</p>
      <p className="mt-1 text-[13px] text-muted">Select a candidate to inspect its timing and evidence.</p>
    </div>
  );
}

function DetailBody({ index, c, view, onBack }: { index: DocIndex; c: DocCandidate; view: ViewName; onBack?: () => void }) {
  const ai = index.ai[c.candidate_id];
  const entry = index.entryById[view].get(c.candidate_id);
  const model = React.useMemo(() => buildTimeline(index, c), [index, c]);
  const evidence = React.useMemo(
    () => ({ hook: new Set(ai?.hook_event_ids ?? []), cliffhanger: new Set(ai?.cliffhanger_event_ids ?? []) }),
    [ai],
  );
  const [activeId, setActiveId] = React.useState<string | null>(null);
  const showAi = view === "ai" && ai;
  const facts = [
    seconds(c.duration_ms),
    c.segments.length > 1 && `${c.segments.length} segments`,
    c.structural.crosses_scene && "Crosses a scene",
    c.structural.ends_at_episode_end && "Ends the episode",
    !c.structural.clean_end_cut && "Ends mid-shot",
  ].filter(Boolean) as string[];

  return (
    <>
      {onBack && (
        <Button variant="ghost" size="sm" className="-ml-2.5 mb-3" onClick={onBack}>
          <ArrowLeft /> All candidates
        </Button>
      )}

      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] font-medium text-muted">
            <span className="font-semibold text-ink-soft">Episode {c.episode_number}</span>
            <Dot />
            <span className="tabular">
              {entry?.rank != null ? `Rank ${entry.rank}` : entry?.alternativeOf ? "Alternative cut" : "Not in this view"}
            </span>
            {showAi && <Badge tone="accent" className="ml-1"><BadgeCheck /> AI-reviewed</Badge>}
          </p>
          <h2 className="mt-1.5 text-[22px] leading-tight font-bold tracking-[-0.02em] text-ink tabular sm:text-[24px]">
            {timecode(c.start_ms)} <span className="font-medium text-faint">→</span> {timecode(c.end_ms)}
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted tabular">
            {facts.map((f, i) => (
              <React.Fragment key={f}>
                {i > 0 && <Dot />}
                <span className={cn(i === 0 && "font-semibold text-ink-soft")}>{f}</span>
              </React.Fragment>
            ))}
          </p>
        </div>
        <ReviewControls id={c.candidate_id} />
      </header>

      <NoteField id={c.candidate_id} />

      <section aria-label="Score" className="mt-5 border-t border-line pt-5">
        {showAi ? <AiScore j={ai} /> : <BaselineScore c={c} hasAi={Boolean(ai)} />}
      </section>

      {showAi && (
        <section aria-label="AI judgement" className="mt-5 grid gap-x-10 gap-y-5 md:grid-cols-2">
          <Prose title="Why it works" text={ai.why_it_works} />
          <Prose title="Weakness" text={ai.weakness}>
            {ai.warnings.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {ai.warnings.map((w) => <Badge key={w} tone="neutral">{warningLabel(w)}</Badge>)}
              </div>
            )}
          </Prose>
          <Judgement title="Hook" window="first 3 s" text={ai.hook_explanation} ids={ai.hook_event_ids} onPick={setActiveId} />
          <Judgement title="Cliffhanger" window="final 5 s" text={ai.cliffhanger_explanation} ids={ai.cliffhanger_event_ids} onPick={setActiveId} />
        </section>
      )}

      <Section label="Timeline" title="Screenplay map" aside={<TimelineLegend evidence={Boolean(showAi)} />}>
        <Timeline model={model} evidence={evidence} activeId={activeId} onActivate={setActiveId} />
        <EventInspector id={activeId} model={model} evidence={evidence} onActivate={setActiveId} />
      </Section>

      <Section label="Screenplay excerpt" title="Script">
        <Excerpt model={model} evidence={evidence} activeId={activeId} onActivate={setActiveId} />
      </Section>

      <Cuts index={index} view={view} c={c} />
      <Details c={c} ai={ai} />
    </>
  );
}

function Dot() {
  return <span aria-hidden className="size-[3px] rounded-full bg-faint" />;
}

function Section({ label, title, aside, children }: { label: string; title?: string; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section aria-label={label} className="mt-7 border-t border-line pt-5">
      {(title || aside) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          {title && <h3 className="text-[14px] font-bold tracking-[-0.01em] text-ink">{title}</h3>}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

/* Review ------------------------------------------------------------------------------ */

const DECISIONS: { value: Decision; label: string; icon: React.ReactNode; on: string; key: string }[] = [
  { value: "accepted", label: "Accept", icon: <CircleCheck />, on: "bg-ok-soft text-ok ring-ok/30", key: "A" },
  { value: "later", label: "Later", icon: <Clock3 />, on: "bg-later-soft text-ink ring-line-strong", key: "L" },
  { value: "rejected", label: "Reject", icon: <CircleX />, on: "bg-no-soft text-no ring-no/30", key: "R" },
];

function ReviewControls({ id }: { id: string }) {
  const { state, dispatch } = useApp();
  const current = state.reviews[id]?.decision ?? null;
  return (
    <div role="group" aria-label="Review decision" className="flex gap-1 rounded-lg border border-line bg-surface p-1">
      {DECISIONS.map((d) => {
        const on = current === d.value;
        return (
          <Tip key={d.value} content={`${d.label} · press ${d.key}`}>
            <button
              type="button"
              aria-pressed={on}
              onClick={() => dispatch({ type: "decide", id, decision: on ? null : d.value })}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-[13px] font-semibold text-ink-soft transition-colors hover:bg-subtle hover:text-ink [&_svg]:size-4",
                on && cn("ring-1 hover:bg-[unset]", d.on),
              )}
            >
              {d.icon}
              {d.label}
            </button>
          </Tip>
        );
      })}
    </div>
  );
}

function NoteField({ id }: { id: string }) {
  const { state, dispatch } = useApp();
  const stored = state.reviews[id]?.note ?? "";
  const [open, setOpen] = React.useState(Boolean(stored));
  const [value, setValue] = React.useState(stored);
  React.useEffect(() => {
    setValue(stored);
    setOpen(Boolean(stored));
  }, [id, stored]);
  React.useEffect(() => {
    if (value === stored) return;
    const t = setTimeout(() => dispatch({ type: "note", id, note: value }), 350);
    return () => clearTimeout(t);
  }, [value, stored, id, dispatch]);
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-3 inline-flex items-center gap-1.5 rounded-md text-[13px] font-semibold text-muted transition-colors hover:text-accent"
      >
        <StickyNote className="size-4" /> Add note
      </button>
    );
  }
  return (
    <div className="mt-3 max-w-[640px]">
      <label htmlFor={`note-${id}`} className="sr-only">Private note</label>
      <Textarea
        id={`note-${id}`}
        rows={2}
        maxLength={500}
        placeholder="Note (kept in this browser)"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => value !== stored && dispatch({ type: "note", id, note: value })}
      />
    </div>
  );
}

/* Scores ------------------------------------------------------------------------------ */

/** "a, b and c" for short natural-language lists. */
function listText(items: string[]): string {
  return items.length < 2 ? (items[0] ?? "") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function ScoreLine({ value, scale, label, children }: { value: string; scale: string; label: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
      <p className="flex items-baseline gap-1.5">
        <span className="text-[24px] leading-none font-bold tracking-[-0.02em] text-ink tabular">{value}</span>
        <span className="text-[13px] font-medium text-muted tabular">{scale}</span>
        <span className="ml-1 text-[13px] font-semibold text-ink-soft">{label}</span>
      </p>
      {children}
    </div>
  );
}

function AiScore({ j }: { j: AIJudgement }) {
  return (
    <ScoreLine value={Number.isInteger(j.weighted_score) ? String(j.weighted_score) : j.weighted_score.toFixed(1)} scale="/ 100" label="AI score">
      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
        {RUBRIC.map((r) => (
          <Tip key={r.key} content={`${r.hint} · weight ${Math.round((j.rubric_weights[RUBRIC_WEIGHT_KEY[r.key]] ?? 0) * 100)}%`}>
            <div className="flex gap-1.5">
              <dt className="text-muted">{r.label}</dt>
              <dd className="font-semibold text-ink tabular">{j[r.key]}/5</dd>
            </div>
          </Tip>
        ))}
      </dl>
    </ScoreLine>
  );
}

function BaselineScore({ c, hasAi }: { c: DocCandidate; hasAi: boolean }) {
  const { state, dispatch } = useApp();
  const anyAi = (state.doc?.coverage.ai_evaluated ?? 0) > 0; // the list already says so when none exist
  const starts = c.baseline.start_anchors.map(anchorLabel);
  const ends = c.baseline.end_anchors.map(anchorLabel);
  return (
    <>
      <ScoreLine value={c.baseline.priority.toFixed(2)} scale="/ 1" label="Structural score">
        {hasAi && (
          <Button variant="link" size="sm" className="h-auto font-semibold" onClick={() => dispatch({ type: "view", view: "ai" })}>
            View AI review <ChevronRight />
          </Button>
        )}
      </ScoreLine>
      <p className="mt-2 max-w-[640px] text-[13px] leading-relaxed text-ink-soft">
        {starts.length > 0 && <>Starts at {listText(starts)}. </>}
        {ends.length > 0 && <>Ends at {listText(ends)}.</>}
      </p>
      {anyAi && <p className="mt-1 text-[12px] text-muted">Ranked from screenplay structure, not reviewed by AI.</p>}
    </>
  );
}

function Judgement({ title, window, text, ids, onPick }: { title: string; window: string; text: string; ids: string[]; onPick: (id: string) => void }) {
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <h4 className="text-[13px] font-bold text-ink">{title}</h4>
        <span className="text-[12px] font-medium text-muted">{window}</span>
      </div>
      <p className="mt-1.5 text-[14px] leading-relaxed text-ink-soft">{text}</p>
      {ids.length > 0 ? (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {ids.map((id) => (
            <button
              key={id}
              type="button"
              onClick={() => onPick(id)}
              className="h-6 rounded-[5px] bg-accent-wash px-1.5 font-mono text-[11px] text-accent-hover ring-1 ring-accent/15 transition-colors hover:bg-accent-soft"
            >
              {id}
            </button>
          ))}
        </div>
      ) : (
        <p className="mt-2.5 text-[12px] text-muted">No evidence cited (score capped at 1).</p>
      )}
    </div>
  );
}

function Prose({ title, text, children }: { title: string; text: string; children?: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-[13px] font-bold text-ink">{title}</h4>
      <p className="mt-2 text-[14px] leading-relaxed text-ink-soft">{text}</p>
      {children}
    </div>
  );
}

function Excerpt({ model, evidence, activeId, onActivate }: { model: Model; evidence: Evidence; activeId: string | null; onActivate: (id: string) => void }) {
  const before = model.events.filter((p) => !p.inClip && p.event.end_ms <= model.clip[0]);
  const inside = model.events.filter((p) => p.inClip);
  const after = model.events.filter((p) => !p.inClip && p.event.start_ms >= model.clip[1]);
  const block = (title: string, range: string, list: typeof model.events, dim: boolean) =>
    list.length > 0 && (
      <div>
        <p className="sticky top-0 z-[1] flex items-baseline gap-2 border-b border-line bg-surface py-2 text-[12px]">
          <span className={cn("font-bold", dim ? "text-muted" : "text-accent")}>{title}</span>
          <span className="font-medium text-muted tabular">{range}</span>
        </p>
        <ol className="py-1.5">
          {list.map((p) => (
            <li key={p.event.id}>
              <ExcerptLine
                ev={p.event}
                dim={dim}
                window={!dim && (p.inHook || p.inEnding)}
                evidence={evidence.hook.has(p.event.id) || evidence.cliffhanger.has(p.event.id)}
                active={activeId === p.event.id}
                onActivate={onActivate}
              />
            </li>
          ))}
        </ol>
      </div>
    );
  return (
    <div className="space-y-2">
      {block("Before the clip", `from ${timecode(model.lo)}`, before, true)}
      {block("In the clip", `${timecode(model.clip[0])} – ${timecode(model.clip[1])}`, inside, false)}
      {block("After the clip", `to ${timecode(model.hi)}`, after, true)}
    </div>
  );
}

function ExcerptLine({ ev, dim, window, evidence, active, onActivate }: { ev: DocEvent; dim: boolean; window: boolean; evidence: boolean; active: boolean; onActivate: (id: string) => void }) {
  const camera = ev.type === "CAMERA";
  const tag = camera ? "Shot" : ev.type === "SOUND_FX" ? "Sound" : null;
  return (
    <button
      type="button"
      data-excerpt-line
      data-active={active || undefined}
      onClick={() => onActivate(ev.id)}
      className={cn(
        "relative grid w-full grid-cols-[76px_1fr] gap-3 rounded-md py-1.5 pr-2 pl-3 text-left transition-colors hover:bg-canvas",
        active && "bg-accent-wash hover:bg-accent-wash",
        dim && "opacity-55",
      )}
    >
      {window && <span className="absolute top-1.5 bottom-1.5 left-0 w-[2px] rounded-full bg-accent" aria-hidden />}
      <span className="pt-[2px] text-[12px] font-medium text-muted tabular">{timecode(ev.start_ms)}</span>
      <span className={cn("text-[14px] leading-relaxed", camera ? "text-[13px] text-muted" : "text-ink")}>
        {tag && <span className="mr-2 text-[10.5px] font-bold tracking-[0.04em] text-muted uppercase">{tag}</span>}
        {ev.speaker && (
          <span className="font-bold">
            {ev.speaker}
            {ev.annotations.length > 0 && <span className="font-medium text-muted"> ({ev.annotations.join(", ")})</span>}{" "}
          </span>
        )}
        <span className={cn(ev.type === "ACTION" && "text-ink-soft italic")}>{ev.text}</span>
        {evidence && <Badge tone="accent" className="ml-2 align-[1px]">AI evidence</Badge>}
      </span>
    </button>
  );
}

/* Cuts and references ---------------------------------------------------------------- */

function Cuts({ index, view, c }: { index: DocIndex; view: ViewName; c: DocCandidate }) {
  const { dispatch } = useApp();
  const family = cutFamily(index, view, c.candidate_id);
  if (family.length < 2) return null;
  return (
    <section aria-label="Alternative cuts" className="mt-7 border-t border-line pt-5">
      <div className="mb-3 flex items-baseline justify-between gap-4">
        <h3 className="text-[14px] font-bold tracking-[-0.01em] text-ink">Cuts of this moment</h3>
        <span className="text-[12px] font-medium text-muted">{family.length} cuts</span>
      </div>
      <ul className="divide-y divide-line overflow-hidden rounded-lg border border-line">
        {family.map((id, i) => {
          const cut = index.candidates.get(id)!;
          const score = view === "ai" ? index.ai[id]?.weighted_score : cut.baseline.priority;
          const current = id === c.candidate_id;
          return (
            <li key={id}>
              <button
                type="button"
                aria-current={current}
                onClick={() => dispatch({ type: "select", id })}
                className={cn(
                  "grid w-full grid-cols-[1fr_auto] items-center gap-3 px-4 py-2.5 text-left text-[13px] transition-colors hover:bg-canvas",
                  current && "bg-accent-wash hover:bg-accent-wash",
                )}
              >
                <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5">
                  <span className={cn("font-semibold tabular", current ? "text-accent-hover" : "text-ink")}>
                    {timecode(cut.start_ms)} → {timecode(cut.end_ms)}
                  </span>
                  <span className="text-muted tabular">{seconds(cut.duration_ms)}</span>
                  <span className="text-[12px] font-medium text-muted">{i === 0 ? "Primary" : "Alternative"}</span>
                </span>
                <span className="font-semibold text-ink-soft tabular">
                  {score === undefined ? "" : view === "ai" ? `${score} / 100` : score.toFixed(2)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Details({ c, ai }: { c: DocCandidate; ai?: AIJudgement }) {
  const copy = (text: string) => void navigator.clipboard?.writeText(text).catch(() => undefined);
  return (
    <details className="group mt-7 border-t border-line">
      <summary className="flex cursor-pointer list-none items-center justify-between py-4 text-[14px] font-bold text-ink">
        Details
        <ChevronRight className="size-4 text-muted transition-transform group-open:rotate-90" />
      </summary>
      <div className="pb-2">
        <h4 className="text-[12px] font-semibold text-muted">Structural score breakdown</h4>
        <dl className="mt-2 grid grid-cols-2 gap-x-8 gap-y-1.5 text-[13px] sm:grid-cols-3">
          {Object.entries(c.baseline.components).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3">
              <dt className="text-ink-soft">{COMPONENT_LABELS[k] ?? k}</dt>
              <dd className="font-semibold text-ink tabular">{v.toFixed(2)}</dd>
            </div>
          ))}
        </dl>
        <dl className="mt-5 grid gap-x-10 gap-y-4 text-[12.5px] sm:grid-cols-2">
          <Ref label="Candidate" value={c.candidate_id} onCopy={copy} />
          <Ref label="Segments" value={c.segments.map((s) => `${s.start_timestamp}–${s.end_timestamp}`).join(", ")} />
          <Ref label="Opening window events" value={c.source_events.opening_window.join(", ") || "none"} />
          <Ref label="Ending window events" value={c.source_events.ending_window.join(", ") || "none"} />
          {ai && <Ref label="AI hook evidence" value={ai.hook_event_ids.join(", ") || "none"} />}
          {ai && <Ref label="AI cliffhanger evidence" value={ai.cliffhanger_event_ids.join(", ") || "none"} />}
          {ai && <Ref label="AI model" value={`${ai.model_reported ?? ai.model_requested} · ${ai.provider ?? ""} · prompt ${ai.prompt_version}`} />}
          <Ref label="Structural method" value={`${c.baseline.method}${c.baseline.selection_reason ? ` · ${c.baseline.selection_reason}` : ""}`} />
        </dl>
      </div>
    </details>
  );
}

function Ref({ label, value, onCopy }: { label: string; value: string; onCopy?: (t: string) => void }) {
  return (
    <div className="min-w-0">
      <dt className="text-[12px] font-semibold text-muted">{label}</dt>
      <dd className="mt-1 flex items-start gap-1.5 font-mono text-[11.5px] break-all text-ink-soft">
        {value}
        {onCopy && (
          <button type="button" aria-label={`Copy ${label}`} onClick={() => onCopy(value)} className="rounded text-muted hover:text-accent">
            <Copy className="size-3.5" />
          </button>
        )}
      </dd>
    </div>
  );
}
