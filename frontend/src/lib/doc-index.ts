import type {
  AIJudgement,
  AnalysisDocument,
  DocCandidate,
  DocEpisode,
  DocEvent,
  ViewName,
} from "@/api/types";

/* ---------------------------------------------------------------------------------------
 * Derived, read-only index over one AnalysisDocument. Built once per document (memoized by
 * the caller) so filtering and selection never rescan the raw response.
 * ------------------------------------------------------------------------------------- */

export interface Entry {
  id: string;
  rank: number | null; // null for alternative cuts
  alternativeOf: string | null;
}

export interface DocIndex {
  doc: AnalysisDocument;
  candidates: Map<string, DocCandidate>;
  episodes: Map<number, DocEpisode>;
  eventById: Map<string, DocEvent>;
  ai: Record<string, AIJudgement>;
  entries: Record<ViewName, Entry[]>;
  entryById: Record<ViewName, Map<string, Entry>>;
  /** primary id -> its alternative ids, per view */
  alternatives: Record<ViewName, Map<string, string[]>>;
  warnings: string[]; // AI warning codes present in this document
  searchText(id: string): string;
}

export function buildIndex(doc: AnalysisDocument): DocIndex {
  const candidates = new Map(doc.candidates.map((c) => [c.candidate_id, c]));
  const episodes = new Map(doc.episodes.map((e) => [e.episode_number, e]));
  const eventById = new Map(doc.events.map((e) => [e.id, e]));
  const entries = {} as Record<ViewName, Entry[]>;
  const entryById = {} as Record<ViewName, Map<string, Entry>>;
  const alternatives = {} as Record<ViewName, Map<string, string[]>>;
  for (const view of ["ai", "coverage"] as const) {
    const list: Entry[] = doc.views[view].order.map((id) => {
      const c = candidates.get(id)!;
      return view === "ai"
        ? { id, rank: c.ai_rank ?? null, alternativeOf: c.ai_alternative_of ?? null }
        : { id, rank: c.coverage_rank ?? null, alternativeOf: c.coverage_alternative_of ?? null };
    });
    entries[view] = list;
    entryById[view] = new Map(list.map((e) => [e.id, e]));
    const alts = new Map<string, string[]>();
    for (const e of list) {
      if (e.alternativeOf) alts.set(e.alternativeOf, [...(alts.get(e.alternativeOf) ?? []), e.id]);
    }
    alternatives[view] = alts;
  }
  const warnings = [...new Set(Object.values(doc.ai_evaluations).flatMap((j) => j.warnings))].sort();

  const cache = new Map<string, string>();
  const searchText = (id: string): string => {
    let text = cache.get(id);
    if (text === undefined) {
      const c = candidates.get(id)!;
      const ep = episodes.get(c.episode_number)!;
      const parts = [id, `episode ${c.episode_number}`, `ep ${c.episode_number}`];
      for (const ev of doc.events.slice(ep.event_range[0], ep.event_range[1])) {
        if (ev.start_ms < c.end_ms && ev.end_ms > c.start_ms) parts.push(ev.speaker ?? "", ev.text);
      }
      const j = doc.ai_evaluations[id];
      if (j) parts.push(j.hook_explanation, j.cliffhanger_explanation, j.why_it_works, j.weakness);
      text = parts.join(" ").toLowerCase();
      cache.set(id, text);
    }
    return text;
  };
  return {
    doc,
    candidates,
    episodes,
    eventById,
    ai: doc.ai_evaluations,
    entries,
    entryById,
    alternatives,
    warnings,
    searchText,
  };
}

/* --------------------------------------------------------------------------------------- */
/* Review decisions (browser-local)                                                         */
/* --------------------------------------------------------------------------------------- */

export type Decision = "accepted" | "later" | "rejected";
export interface Review {
  decision: Decision | null;
  note: string;
  updatedAt: string;
}
export type Reviews = Record<string, Review>;

/* --------------------------------------------------------------------------------------- */
/* Filters                                                                                  */
/* --------------------------------------------------------------------------------------- */

export type DurationBand = "any" | "short" | "medium" | "long";
export type DecisionFilter = "any" | "undecided" | "decided" | Decision;

export interface Filters {
  episode: number | null;
  duration: DurationBand;
  status: "any" | "ai_evaluated" | "not_evaluated" | "failed";
  warning: string | null;
  decision: DecisionFilter;
  alternatives: boolean;
}

export const DEFAULT_FILTERS: Filters = {
  episode: null,
  duration: "any",
  status: "any",
  warning: null,
  decision: "any",
  alternatives: false,
};

export const DURATION_BANDS: Record<DurationBand, { label: string; test: (s: number) => boolean }> = {
  any: { label: "Any length", test: () => true },
  short: { label: "Up to 30 s", test: (s) => s <= 30 },
  medium: { label: "30–45 s", test: (s) => s > 30 && s <= 45 },
  long: { label: "Over 45 s", test: (s) => s > 45 },
};

export function activeFilterCount(f: Filters): number {
  return (
    Number(f.episode !== null) +
    Number(f.duration !== "any") +
    Number(f.status !== "any") +
    Number(f.warning !== null) +
    Number(f.decision !== "any") +
    Number(f.alternatives)
  );
}

export function filterEntries(
  index: DocIndex,
  view: ViewName,
  filters: Filters,
  query: string,
  reviews: Reviews,
): Entry[] {
  const q = query.trim().toLowerCase();
  const terms = q ? q.split(/\s+/) : [];
  const band = DURATION_BANDS[filters.duration].test;
  return index.entries[view].filter((e) => {
    if (e.rank === null && !filters.alternatives) return false;
    const c = index.candidates.get(e.id)!;
    if (filters.episode !== null && c.episode_number !== filters.episode) return false;
    if (!band(c.duration_ms / 1000)) return false;
    if (filters.status !== "any" && c.evaluation_status !== filters.status) return false;
    if (filters.warning !== null && !(index.ai[e.id]?.warnings ?? []).includes(filters.warning)) {
      return false;
    }
    const decision = reviews[e.id]?.decision ?? null;
    if (filters.decision === "undecided" && decision !== null) return false;
    if (filters.decision === "decided" && decision === null) return false;
    if (["accepted", "later", "rejected"].includes(filters.decision) && decision !== filters.decision) {
      return false;
    }
    if (terms.length) {
      const text = index.searchText(e.id);
      if (!terms.every((t) => text.includes(t))) return false;
    }
    return true;
  });
}

/** The candidate's cut family in a view: its primary first, then the alternatives. */
export function cutFamily(index: DocIndex, view: ViewName, id: string): string[] {
  const entry = index.entryById[view].get(id);
  if (!entry) return [id];
  const primary = entry.alternativeOf ?? id;
  return [primary, ...(index.alternatives[view].get(primary) ?? [])];
}

/* --------------------------------------------------------------------------------------- */
/* Timeline                                                                                 */
/* --------------------------------------------------------------------------------------- */

export const CONTEXT_MS = 10_000;
export const HOOK_MS = 3_000;
export const ENDING_MS = 5_000;
export const LANES = ["DIALOGUE", "ACTION", "SOUND_FX", "CAMERA"] as const;
export type Lane = (typeof LANES)[number];

export interface PlacedEvent {
  event: DocEvent;
  row: number; // sub-row within its lane, for overlapping events
  inClip: boolean;
  inHook: boolean;
  inEnding: boolean;
}

export interface TimelineModel {
  lo: number;
  hi: number;
  clip: [number, number];
  hook: [number, number];
  ending: [number, number];
  lanes: { lane: Lane; rows: number; events: PlacedEvent[] }[];
  events: PlacedEvent[]; // chronological, for the excerpt
  ticks: number[];
  scenes: { scene_number: number; heading: string; start_ms: number; end_ms: number }[];
}

/** Half-open [a, b) intersection, with zero-length events treated as instants (as the
 *  backend does). */
export function intersects(ev: { start_ms: number; end_ms: number }, a: number, b: number): boolean {
  if (ev.start_ms === ev.end_ms) return a <= ev.start_ms && ev.start_ms < b;
  return ev.start_ms < b && ev.end_ms > a;
}

export function buildTimeline(index: DocIndex, c: DocCandidate): TimelineModel {
  const ep = index.episodes.get(c.episode_number)!;
  const lo = Math.max(0, c.start_ms - CONTEXT_MS);
  const hi = Math.min(ep.duration_ms, c.end_ms + CONTEXT_MS);
  const hook: [number, number] = [c.start_ms, Math.min(c.end_ms, c.start_ms + HOOK_MS)];
  const ending: [number, number] = [Math.max(c.start_ms, c.end_ms - ENDING_MS), c.end_ms];
  const events = index.doc.events
    .slice(ep.event_range[0], ep.event_range[1])
    .filter((ev) => intersects(ev, lo, hi))
    .map<PlacedEvent>((event) => ({
      event,
      row: 0,
      inClip: intersects(event, c.start_ms, c.end_ms),
      inHook: intersects(event, hook[0], hook[1]),
      inEnding: intersects(event, ending[0], ending[1]),
    }));
  const lanes = LANES.map((lane) => {
    const inLane = events.filter((p) => p.event.type === lane);
    const rowEnds: number[] = [];
    for (const p of inLane) {
      let row = rowEnds.findIndex((end) => end <= p.event.start_ms);
      if (row === -1) row = rowEnds.length;
      rowEnds[row] = Math.max(p.event.end_ms, p.event.start_ms + 1);
      p.row = row;
    }
    return { lane, rows: Math.max(1, rowEnds.length), events: inLane };
  });
  const span = hi - lo;
  const step = span <= 30_000 ? 2_000 : span <= 60_000 ? 5_000 : span <= 120_000 ? 10_000 : 20_000;
  const ticks: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi; t += step) ticks.push(t);
  const scenes = ep.scenes.filter((s) => s.start_ms < hi && s.end_ms > lo);
  return { lo, hi, clip: [c.start_ms, c.end_ms], hook, ending, lanes, events, ticks, scenes };
}
