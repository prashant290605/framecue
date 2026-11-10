import { SCHEMA_VERSION, type AnalysisDocument } from "@/api/types";

export type Validation = { ok: true; doc: AnalysisDocument } | { ok: false; reason: string };

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);
const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0;

/**
 * Structural validation of an AnalysisDocument: used for API responses, imported files and
 * restored IndexedDB records. It checks the parts the UI relies on and cross-references
 * (views and AI judgements must point at real candidates; evidence at real events), so a
 * corrupt or tampered file is rejected instead of rendering nonsense.
 */
export function validateDocument(input: unknown): Validation {
  const fail = (reason: string): Validation => ({ ok: false, reason });
  if (!isObj(input)) return fail("not a JSON object");
  // Documents saved or exported earlier use the same v1 format under an earlier id.
  const value =
    typeof input.schema_version === "string" && /^[a-z]+-analysis-v1$/.test(input.schema_version)
      ? { ...input, schema_version: SCHEMA_VERSION }
      : input;
  if (value.schema_version !== SCHEMA_VERSION) {
    return fail(`unsupported schema version (expected ${SCHEMA_VERSION})`);
  }
  const { summary, coverage, episodes, events, candidates, ai_evaluations, views } = value;
  if (!isObj(summary) || !isInt(summary.candidates) || !isObj(summary.input)) {
    return fail("missing summary");
  }
  if (!isObj(coverage) || !isInt(coverage.ai_evaluated)) return fail("missing coverage");
  if (!Array.isArray(episodes) || !Array.isArray(events) || !Array.isArray(candidates)) {
    return fail("missing episodes, events or candidates");
  }
  if (!isObj(ai_evaluations) || !isObj(views) || !isObj(views.ai) || !isObj(views.coverage)) {
    return fail("missing views or AI evaluations");
  }
  const eventIds = new Set<string>();
  for (const e of events) {
    if (!isObj(e) || typeof e.id !== "string" || !isInt(e.start_ms) || !isInt(e.end_ms)) {
      return fail("malformed event");
    }
    eventIds.add(e.id);
  }
  for (const ep of episodes) {
    if (!isObj(ep) || !isInt(ep.episode_number) || !Array.isArray(ep.event_range)) {
      return fail("malformed episode");
    }
    const [a, b] = ep.event_range as unknown[];
    if (!isInt(a) || !isInt(b) || a > b || b > events.length) return fail("bad event range");
  }
  const ids = new Set<string>();
  for (const c of candidates) {
    if (
      !isObj(c) ||
      typeof c.candidate_id !== "string" ||
      !isInt(c.start_ms) ||
      !isInt(c.end_ms) ||
      !Array.isArray(c.segments) ||
      c.segments.length === 0 ||
      !isObj(c.baseline) ||
      typeof c.baseline.priority !== "number"
    ) {
      return fail("malformed candidate");
    }
    if ((c.end_ms as number) <= (c.start_ms as number)) return fail("candidate ends before it starts");
    ids.add(c.candidate_id);
  }
  if (ids.size !== candidates.length || summary.candidates !== candidates.length) {
    return fail("candidate count mismatch");
  }
  for (const name of ["ai", "coverage"] as const) {
    const order = (views[name] as Record<string, unknown>).order;
    if (!Array.isArray(order) || order.some((id) => typeof id !== "string" || !ids.has(id))) {
      return fail(`view ${name} references unknown candidates`);
    }
  }
  for (const [id, j] of Object.entries(ai_evaluations)) {
    if (!ids.has(id) || !isObj(j)) return fail("AI evaluation for an unknown candidate");
    const score = j.weighted_score;
    if (typeof score !== "number" || score < 0 || score > 100) return fail("AI score out of range");
    const cited = [...((j.hook_event_ids as unknown[]) ?? []), ...((j.cliffhanger_event_ids as unknown[]) ?? [])];
    if (cited.some((e) => typeof e !== "string" || !eventIds.has(e))) {
      return fail("AI evidence references an unknown event");
    }
  }
  return { ok: true, doc: value as unknown as AnalysisDocument };
}
