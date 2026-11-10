import { m, useReducedMotion } from "motion/react";
import { ArrowLeft, Check, ChevronRight, Clock3, Copy, Sparkles, StickyNote, X } from "lucide-react";
import * as React from "react";
import type { AIJudgement, DocCandidate, DocEvent, ViewName } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Tip } from "@/components/ui/overlays";
import { buildTimeline, cutFamily, type Decision, type DocIndex, type Lane } from "@/lib/doc-index";
import { anchorLabel, COMPONENT_LABELS, LANE_LABELS, RUBRIC, RUBRIC_WEIGHT_KEY, warningLabel } from "@/lib/labels";
import { seconds, timecode } from "@/lib/time";
import { cn } from "@/lib/utils";
import { useApp } from "@/state/app-state";
import { Timeline } from "./Timeline";

export function Detail({ onBack }: { onBack?: () => void }) {
  const { state } = useApp();
  const { index, selectedId, view } = state;
  const reduce = useReducedMotion();
  if (!index) return null;
  const c = selectedId ? index.candidates.get(selectedId) : undefined;
  if (!c) {
    return (
      <div className="flex h-full items-center justify-center p-10 text-center text-[13px] text-muted">
        Select a candidate to inspect its timing and evidence.
      </div>
    );
  }
  // Keyed by candidate: the new cut renders immediately (no exit wait), with a short fade-in.
  return (
    <m.article
      key={c.candidate_id}
      initial={reduce ? false : { opacity: 0, y: 3 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.14, ease: "easeOut" }}
      className="mx-auto w-full max-w-[880px] px-5 pt-5 pb-16 sm:px-7"
      aria-label={`Candidate ${c.candidate_id}`}
    >
      <DetailBody index={index} c={c} view={view} onBack={onBack} />
    </m.article>
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

  return (
    <>
      {onBack && (
        <Button variant="ghost" size="sm" className="-ml-2 mb-2" onClick={onBack}>
          <ArrowLeft /> All candidates
        </Button>
      )}
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
            <span className="font-medium text-ink-soft">Episode {c.episode_number}</span>
            <span aria-hidden>·</span>
            {entry?.rank != null ? (
              <span>
                {view === "ai" ? "AI" : "Full-show"} rank <span className="tabular font-medium text-ink-soft">#{entry.rank}</span>
              </span>
            ) : entry?.alternativeOf ? (
              <span>Alternative cut</span>
            ) : (
              <span>Not in this view</span>
            )}
            {showAi ? (
              <Badge tone="amber"><Sparkles /> AI-reviewed</Badge>
            ) : (
              <Badge tone="outline">Unverified suggestion</Badge>
            )}
          </div>
          <h2 className="mt-1.5 font-mono text-[22px] font-medium tracking-tight text-ink tabular sm:text-[26px]">
            {timecode(c.start_ms)} <span className="text-faint">→</span> {timecode(c.end_ms)}
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px] text-muted">
            <span className="flex items-center gap-1"><Clock3 className="size-3.5" /> <span className="tabular text-ink-soft">{seconds(c.duration_ms)}</span></span>
            {c.segments.length > 1 && <span>{c.segments.length} segments</span>}
            {c.structural.crosses_scene && <span>Crosses a scene</span>}
            {c.structural.ends_at_episode_end && <span>Ends the episode</span>}
            {!c.structural.clean_end_cut && <span>Ends mid-shot</span>}
          </p>
        </div>
        <ReviewControls id={c.candidate_id} />
      </header>

      <NoteField id={c.candidate_id} />

      <section aria-label="Score" className="mt-6">
        {showAi ? <AiScore j={ai} /> : <BaselineScore c={c} hasAi={Boolean(ai)} />}
      </section>

      {showAi ? (
        <section aria-label="AI judgement" className="mt-6 grid gap-3 md:grid-cols-2">
          <Judgement title="Hook · first 3 s" score={ai.hook_score} text={ai.hook_explanation} ids={ai.hook_event_ids} onPick={setActiveId} />
          <Judgement title="Cliffhanger · final 5 s" score={ai.cliffhanger_score} text={ai.cliffhanger_explanation} ids={ai.cliffhanger_event_ids} onPick={setActiveId} />
          <Prose title="Why it works" text={ai.why_it_works} />
          <Prose title="Weakness" text={ai.weakness}>
            {ai.warnings.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {ai.warnings.map((w) => <Badge key={w} tone="neutral">{warningLabel(w)}</Badge>)}
              </div>
            )}
          </Prose>
        </section>
      ) : (
        <section aria-label="Structural signals" className="mt-6 rounded-lg border border-line bg-surface p-4">
          <h3 className="text-[13px] font-semibold">Structural signals</h3>
          <p className="mt-1 text-[13px] text-muted">
            {ai
              ? "An AI review exists for this cut; switch to AI-reviewed to read it. Below are the baseline's structural reasons only."
              : "No AI review for this candidate. These are the baseline's structural reasons, not an editorial judgement."}
          </p>
          <dl className="mt-3 grid gap-x-6 gap-y-2 text-[13px] sm:grid-cols-2">
            <div><dt className="text-muted">Starts at</dt><dd className="text-ink-soft">{c.baseline.start_anchors.map(anchorLabel).join(", ")}</dd></div>
            <div><dt className="text-muted">Ends at</dt><dd className="text-ink-soft">{c.baseline.end_anchors.map(anchorLabel).join(", ")}</dd></div>
          </dl>
        </section>
      )}

      <section aria-label="Timeline" className="mt-8">
        <SectionTitle>Screenplay timeline</SectionTitle>
        <div className="mt-3 rounded-lg border border-line bg-surface p-4">
          <Timeline model={model} evidence={evidence} activeId={activeId} onActivate={setActiveId} />
          <ActiveEvent id={activeId} model={model} evidence={evidence} />
        </div>
      </section>

      <section aria-label="Screenplay excerpt" className="mt-8">
        <SectionTitle>Screenplay excerpt</SectionTitle>
        <Excerpt model={model} evidence={evidence} activeId={activeId} onActivate={setActiveId} />
      </section>

      <Cuts index={index} view={view} c={c} />
      <SourceRefs c={c} ai={ai} />
    </>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h3 className="text-[12px] font-semibold tracking-[0.06em] text-muted uppercase">{children}</h3>;
}

/* Review ------------------------------------------------------------------------------ */

const DECISIONS: { value: Decision; label: string; icon: React.ReactNode; on: string; key: string }[] = [
  { value: "accepted", label: "Accept", icon: <Check />, on: "border-ok/40 bg-ok-soft text-ok", key: "A" },
  { value: "later", label: "Later", icon: <Clock3 />, on: "border-amber/50 bg-amber-soft text-amber-ink", key: "L" },
  { value: "rejected", label: "Reject", icon: <X />, on: "border-no/35 bg-no-soft text-no", key: "R" },
];

function ReviewControls({ id }: { id: string }) {
  const { state, dispatch } = useApp();
  const current = state.reviews[id]?.decision ?? null;
  return (
    <div role="group" aria-label="Review decision" className="flex rounded-md border border-line bg-surface p-0.5 shadow-card">
      {DECISIONS.map((d) => {
        const on = current === d.value;
        return (
          <Tip key={d.value} content={`${d.label} (${d.key})`}>
            <button
              type="button"
              aria-pressed={on}
              onClick={() => dispatch({ type: "decide", id, decision: on ? null : d.value })}
              className={cn(
                "inline-flex h-7 items-center gap-1 rounded-[5px] border border-transparent px-2.5 text-[12.5px] font-medium text-ink-soft transition-colors hover:bg-sunken [&_svg]:size-3.5",
                on && d.on,
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
      <button type="button" onClick={() => setOpen(true)} className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-muted hover:text-ink">
        <StickyNote className="size-3.5" /> Add a private note
      </button>
    );
  }
  return (
    <div className="mt-3">
      <label htmlFor={`note-${id}`} className="sr-only">Private note</label>
      <Textarea
        id={`note-${id}`}
        rows={2}
        maxLength={500}
        placeholder="Private note (saved in this browser only)"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => value !== stored && dispatch({ type: "note", id, note: value })}
      />
    </div>
  );
}

/* Scores ------------------------------------------------------------------------------ */

function AiScore({ j }: { j: AIJudgement }) {
  return (
    <div className="grid gap-5 rounded-lg border border-line bg-surface p-4 sm:grid-cols-[180px_1fr]">
      <div>
        <p className="text-[12px] text-muted">AI weighted score</p>
        <p className="mt-0.5 font-display text-[40px] leading-none font-semibold tabular">
          {Number.isInteger(j.weighted_score) ? j.weighted_score : j.weighted_score.toFixed(1)}
          <span className="ml-1 font-sans text-[14px] font-normal text-faint">/ 100</span>
        </p>
        <p className="mt-2 text-[11.5px] leading-snug text-muted">
          Editorial estimate from a validated model review · <span className="font-mono">ai_weighted_v1</span>
        </p>
      </div>
      <div className="space-y-2.5">
        {RUBRIC.map((r) => (
          <div key={r.key} className="grid grid-cols-[92px_1fr_44px] items-center gap-3 text-[13px]">
            <Tip content={`${r.hint} · weight ${Math.round((j.rubric_weights[RUBRIC_WEIGHT_KEY[r.key]] ?? 0) * 100)}%`}>
              <span className="text-ink-soft">{r.label}</span>
            </Tip>
            <Meter value={j[r.key]} max={5} label={`${r.label} ${j[r.key]} of 5`} />
            <span className="text-right text-muted tabular">{j[r.key]} / 5</span>
          </div>
        ))}
        <p className="pt-1 text-[11.5px] text-faint">
          {j.model_reported ?? j.model_requested ?? "model"} · {j.provider ?? "provider n/a"} · prompt {j.prompt_version}
        </p>
      </div>
    </div>
  );
}

function BaselineScore({ c, hasAi }: { c: DocCandidate; hasAi: boolean }) {
  const { dispatch } = useApp();
  return (
    <div className="grid gap-5 rounded-lg border border-line bg-surface p-4 sm:grid-cols-[180px_1fr]">
      <div>
        <p className="text-[12px] text-muted">Heuristic priority</p>
        <p className="mt-0.5 font-display text-[40px] leading-none font-semibold text-ink-soft tabular">
          {c.baseline.priority.toFixed(3)}
          <span className="ml-1 font-sans text-[14px] font-normal text-faint">/ 1</span>
        </p>
        <p className="mt-2 text-[11.5px] leading-snug text-muted">
          Unverified ordering signal, not an AI judgement · <span className="font-mono">heuristic_priority_v1</span>
        </p>
        {hasAi && (
          <Button variant="link" size="sm" className="mt-2" onClick={() => dispatch({ type: "view", view: "ai" })}>
            View AI review <ChevronRight />
          </Button>
        )}
      </div>
      <div className="space-y-2.5">
        {Object.entries(c.baseline.components).map(([k, v]) => (
          <div key={k} className="grid grid-cols-[112px_1fr_44px] items-center gap-3 text-[13px]">
            <span className="text-ink-soft">{COMPONENT_LABELS[k] ?? k}</span>
            <Meter value={v} max={1} muted label={`${COMPONENT_LABELS[k] ?? k} ${v.toFixed(2)}`} />
            <span className="text-right text-muted tabular">{v.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Meter({ value, max, label, muted }: { value: number; max: number; label: string; muted?: boolean }) {
  return (
    <div role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} className="h-1.5 overflow-hidden rounded-full bg-sunken">
      <div className={cn("h-full rounded-full", muted ? "bg-ev-action" : "bg-amber")} style={{ width: `${(value / max) * 100}%` }} />
    </div>
  );
}

function Judgement({ title, score, text, ids, onPick }: { title: string; score: number; text: string; ids: string[]; onPick: (id: string) => void }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h4 className="text-[13px] font-semibold">{title}</h4>
        <span className="text-[12px] text-muted tabular">{score} / 5</span>
      </div>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-soft">{text}</p>
      {ids.length > 0 ? (
        <div className="mt-2.5 flex flex-wrap gap-1">
          {ids.map((id) => (
            <button key={id} type="button" onClick={() => onPick(id)} className="rounded-sm bg-amber-wash px-1.5 py-px font-mono text-[11px] text-amber-ink hover:bg-amber-soft">
              {id}
            </button>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-[12px] text-faint">No evidence cited (score capped at 1).</p>
      )}
    </div>
  );
}

function Prose({ title, text, children }: { title: string; text: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <h4 className="text-[13px] font-semibold">{title}</h4>
      <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-soft">{text}</p>
      {children}
    </div>
  );
}

/* Timeline helpers ------------------------------------------------------------------- */

function ActiveEvent({ id, model, evidence }: { id: string | null; model: ReturnType<typeof buildTimeline>; evidence: { hook: Set<string>; cliffhanger: Set<string> } }) {
  const placed = id ? model.events.find((p) => p.event.id === id) : undefined;
  if (!placed) {
    return <p className="mt-3 border-t border-line pt-3 text-[12.5px] text-faint">Select an event bar to read it here. Tab moves through events.</p>;
  }
  const ev = placed.event;
  const tags = [
    placed.inClip ? "In clip" : ev.start_ms < model.clip[0] ? "Before clip" : "After clip",
    placed.inHook && "Hook window",
    placed.inEnding && "Ending window",
    evidence.hook.has(ev.id) && "AI hook evidence",
    evidence.cliffhanger.has(ev.id) && "AI cliffhanger evidence",
  ].filter(Boolean) as string[];
  return (
    <div className="mt-3 border-t border-line pt-3" aria-live="polite">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted">
        <span className="font-medium text-ink-soft">{LANE_LABELS[ev.type as Lane] ?? ev.type}</span>
        <span className="font-mono tabular">{timecode(ev.start_ms)}–{timecode(ev.end_ms)}</span>
        <span className="font-mono text-faint">{ev.id}</span>
        {tags.map((t) => <Badge key={t} tone={t.startsWith("AI") ? "amber" : "neutral"}>{t}</Badge>)}
      </div>
      <p className="mt-1.5 text-[13.5px] text-ink">
        {ev.speaker && <span className="font-semibold">{ev.speaker}{ev.annotations.length ? ` (${ev.annotations.join(", ")})` : ""}: </span>}
        {ev.text}
      </p>
    </div>
  );
}

function Excerpt({ model, evidence, activeId, onActivate }: { model: ReturnType<typeof buildTimeline>; evidence: { hook: Set<string>; cliffhanger: Set<string> }; activeId: string | null; onActivate: (id: string) => void }) {
  const before = model.events.filter((p) => !p.inClip && p.event.end_ms <= model.clip[0]);
  const inside = model.events.filter((p) => p.inClip);
  const after = model.events.filter((p) => !p.inClip && p.event.start_ms >= model.clip[1]);
  const block = (title: string, list: typeof model.events, dim: boolean) =>
    list.length > 0 && (
      <div>
        <p className="sticky top-0 z-[1] -mx-2 border-b border-line/70 bg-[#fcfbf9] px-2 py-1.5 text-[11.5px] font-medium text-muted">{title}</p>
        <ol className="space-y-0.5">
          {list.map((p) => (
            <li key={p.event.id}>
              <ExcerptLine ev={p.event} dim={dim} hook={p.inHook && !dim} ending={p.inEnding && !dim} evidence={evidence.hook.has(p.event.id) || evidence.cliffhanger.has(p.event.id)} active={activeId === p.event.id} onActivate={onActivate} />
            </li>
          ))}
        </ol>
      </div>
    );
  return (
    <div className="mt-3 space-y-3">
      {block(`Before the clip · from ${timecode(model.lo)}`, before, true)}
      {block(`In the clip · ${timecode(model.clip[0])} – ${timecode(model.clip[1])}`, inside, false)}
      {block(`After the clip · to ${timecode(model.hi)}`, after, true)}
    </div>
  );
}

function ExcerptLine({ ev, dim, hook, ending, evidence, active, onActivate }: { ev: DocEvent; dim: boolean; hook: boolean; ending: boolean; evidence: boolean; active: boolean; onActivate: (id: string) => void }) {
  const camera = ev.type === "CAMERA";
  return (
    <button
      type="button"
      onClick={() => onActivate(ev.id)}
      className={cn(
        "grid w-full grid-cols-[74px_1fr] gap-3 rounded-md border-l-2 px-2 py-1.5 text-left transition-colors hover:bg-surface",
        hook || ending ? "border-amber" : "border-transparent",
        active && "bg-surface shadow-card",
        dim && "opacity-60",
      )}
    >
      <span className="pt-px font-mono text-[11.5px] text-faint tabular">{timecode(ev.start_ms)}</span>
      <span className={cn("text-[13.5px] leading-relaxed", camera ? "text-[12.5px] text-muted" : "text-ink")}>
        {camera && <span className="mr-1.5 text-[10.5px] font-semibold tracking-wide text-faint uppercase">Shot</span>}
        {ev.type === "SOUND_FX" && <span className="mr-1.5 text-[10.5px] font-semibold tracking-wide text-faint uppercase">Sound</span>}
        {ev.speaker && (
          <span className="font-semibold">
            {ev.speaker}
            {ev.annotations.length > 0 && <span className="font-normal text-muted"> ({ev.annotations.join(", ")})</span>}{" "}
          </span>
        )}
        <span className={cn(ev.type === "ACTION" && "text-ink-soft italic")}>{ev.text}</span>
        {evidence && <Badge tone="amber" className="ml-1.5 align-middle">AI evidence</Badge>}
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
    <section aria-label="Alternative cuts" className="mt-8">
      <SectionTitle>Cuts of this moment</SectionTitle>
      <ul className="mt-3 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
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
                className={cn("grid w-full grid-cols-[1fr_auto] items-center gap-3 px-4 py-2.5 text-left text-[13px] hover:bg-sunken", current && "bg-amber-wash")}
              >
                <span className="min-w-0">
                  <span className="font-mono tabular">{timecode(cut.start_ms)} → {timecode(cut.end_ms)}</span>
                  <span className="ml-2 text-muted tabular">{seconds(cut.duration_ms)}</span>
                  <span className="ml-2 text-[12px] text-faint">{i === 0 ? "Primary" : "Alternative"}</span>
                </span>
                <span className="text-muted tabular">
                  {score === undefined ? "" : view === "ai" ? `${score} / 100` : score.toFixed(3)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function SourceRefs({ c, ai }: { c: DocCandidate; ai?: AIJudgement }) {
  const copy = (text: string) => void navigator.clipboard?.writeText(text).catch(() => undefined);
  return (
    <details className="group mt-8 rounded-lg border border-line bg-surface">
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-[13px] font-medium">
        Source references
        <ChevronRight className="size-4 text-muted transition-transform group-open:rotate-90" />
      </summary>
      <dl className="grid gap-x-6 gap-y-3 border-t border-line px-4 py-3 text-[12.5px] sm:grid-cols-2">
        <Ref label="Candidate" value={c.candidate_id} onCopy={copy} />
        <Ref label="Segments" value={c.segments.map((s) => `${s.start_timestamp}–${s.end_timestamp}`).join(", ")} />
        <Ref label="Opening window events" value={c.source_events.opening_window.join(", ") || "none"} />
        <Ref label="Ending window events" value={c.source_events.ending_window.join(", ") || "none"} />
        {ai && <Ref label="AI hook evidence" value={ai.hook_event_ids.join(", ") || "none"} />}
        {ai && <Ref label="AI cliffhanger evidence" value={ai.cliffhanger_event_ids.join(", ") || "none"} />}
        {ai && <Ref label="Model" value={`${ai.model_reported ?? ai.model_requested} · ${ai.provider ?? ""} · ${ai.prompt_version}`} />}
        <Ref label="Baseline" value={`${c.baseline.method} · ${c.baseline.selection_reason ?? ""}`} />
      </dl>
    </details>
  );
}

function Ref({ label, value, onCopy }: { label: string; value: string; onCopy?: (t: string) => void }) {
  return (
    <div className="min-w-0">
      <dt className="text-muted">{label}</dt>
      <dd className="mt-0.5 flex items-start gap-1 font-mono text-[11.5px] break-all text-ink-soft">
        {value}
        {onCopy && (
          <button type="button" aria-label={`Copy ${label}`} onClick={() => onCopy(value)} className="text-faint hover:text-ink">
            <Copy className="size-3.5" />
          </button>
        )}
      </dd>
    </div>
  );
}
