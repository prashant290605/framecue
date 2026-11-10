import type { AnalysisDocument, ViewName } from "@/api/types";
import type { DocIndex, Entry, Reviews } from "./doc-index";

/** Same columns as the backend's ai_recommendations.csv / coverage_candidates.csv
 *  (src/framecue/exports.py), plus the editor's decision and note. */
export const RANKED_COLUMNS = [
  "view", "rank", "alternative_of", "candidate_id", "episode", "start_timestamp",
  "end_timestamp", "start_ms", "end_ms", "duration_seconds", "segments", "score_system",
  "score", "evaluation_status", "hook_score", "cliffhanger_score", "standalone_score",
  "pacing_score", "hook_event_ids", "cliffhanger_event_ids", "hook_explanation",
  "cliffhanger_explanation", "why_it_works", "weakness", "ai_warnings", "crosses_scene",
  "ends_at_episode_end", "model", "prompt_version", "label", "editor_decision", "editor_note",
] as const;

type Cell = string | number | boolean | null | undefined | readonly (string | number)[];

/** RFC 4180 quoting, list joining with ';', and the same spreadsheet-formula guard as the
 *  backend (cells starting with = + - @ are prefixed with an apostrophe). */
export function cell(value: Cell): string {
  if (value === null || value === undefined) return "";
  let text: string;
  if (typeof value === "boolean") text = value ? "true" : "false";
  else if (Array.isArray(value)) text = value.join(";");
  else text = String(value);
  if (/^[=+\-@]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function toCsv(columns: readonly string[], rows: Record<string, Cell>[]): string {
  const lines = [columns.join(",")];
  for (const row of rows) lines.push(columns.map((c) => cell(row[c])).join(","));
  return lines.join("\n") + "\n";
}

export function rankedRows(index: DocIndex, view: ViewName, entries: Entry[], reviews: Reviews) {
  const label = index.doc.views[view].label;
  return entries.map((e) => {
    const c = index.candidates.get(e.id)!;
    const j = view === "ai" ? index.ai[e.id] : undefined;
    const review = reviews[e.id];
    return {
      view,
      rank: e.rank,
      alternative_of: e.alternativeOf,
      candidate_id: c.candidate_id,
      episode: c.episode_number,
      start_timestamp: c.start_timestamp,
      end_timestamp: c.end_timestamp,
      start_ms: c.start_ms,
      end_ms: c.end_ms,
      duration_seconds: Number((c.duration_ms / 1000).toFixed(2)),
      segments: c.segments.map((s) => `${s.start_timestamp}-${s.end_timestamp}`),
      score_system: index.doc.views[view].scoring_system,
      score: view === "ai" ? j?.weighted_score : c.baseline.priority,
      evaluation_status: c.evaluation_status,
      hook_score: j?.hook_score,
      cliffhanger_score: j?.cliffhanger_score,
      standalone_score: j?.standalone_score,
      pacing_score: j?.pacing_score,
      hook_event_ids: j?.hook_event_ids ?? [],
      cliffhanger_event_ids: j?.cliffhanger_event_ids ?? [],
      hook_explanation: j?.hook_explanation,
      cliffhanger_explanation: j?.cliffhanger_explanation,
      why_it_works: j?.why_it_works,
      weakness: j?.weakness,
      ai_warnings: j?.warnings ?? [],
      crosses_scene: c.structural.crosses_scene,
      ends_at_episode_end: c.structural.ends_at_episode_end,
      model: j?.model_reported,
      prompt_version: j?.prompt_version,
      label,
      editor_decision: review?.decision ?? null,
      editor_note: review?.note || null,
    } satisfies Record<(typeof RANKED_COLUMNS)[number], Cell>;
  });
}

export function rankedCsv(index: DocIndex, view: ViewName, entries: Entry[], reviews: Reviews): string {
  return toCsv(RANKED_COLUMNS, rankedRows(index, view, entries, reviews));
}

export const SHORTLIST_COLUMNS = [
  "candidate_id", "episode", "start_timestamp", "end_timestamp", "start_ms", "end_ms",
  "duration_seconds", "editor_decision", "editor_note", "ai_score", "ai_score_system",
  "ai_rank", "heuristic_priority", "heuristic_score_system", "coverage_rank",
  "evaluation_status", "model", "prompt_version",
] as const;

export function shortlistItems(index: DocIndex, reviews: Reviews) {
  return [...index.candidates.values()]
    .filter((c) => reviews[c.candidate_id]?.decision || reviews[c.candidate_id]?.note)
    .sort((a, b) => a.episode_number - b.episode_number || a.start_ms - b.start_ms)
    .map((c) => {
      const j = index.ai[c.candidate_id];
      const r = reviews[c.candidate_id];
      return {
        candidate_id: c.candidate_id,
        episode: c.episode_number,
        start_timestamp: c.start_timestamp,
        end_timestamp: c.end_timestamp,
        start_ms: c.start_ms,
        end_ms: c.end_ms,
        duration_seconds: Number((c.duration_ms / 1000).toFixed(2)),
        editor_decision: r?.decision ?? null,
        editor_note: r?.note || null,
        ai_score: j?.weighted_score ?? null,
        ai_score_system: j ? index.doc.views.ai.scoring_system : null,
        ai_rank: c.ai_rank ?? null,
        heuristic_priority: c.baseline.priority,
        heuristic_score_system: index.doc.views.coverage.scoring_system,
        coverage_rank: c.coverage_rank ?? null,
        evaluation_status: c.evaluation_status,
        model: j?.model_reported ?? null,
        prompt_version: j?.prompt_version ?? null,
      };
    });
}

export function shortlistCsv(index: DocIndex, reviews: Reviews): string {
  return toCsv(SHORTLIST_COLUMNS, shortlistItems(index, reviews));
}

export function shortlistJson(index: DocIndex, reviews: Reviews, now = new Date()): string {
  const s = index.doc.summary;
  return JSON.stringify(
    {
      format: "framecue-shortlist-v1",
      exported_at: now.toISOString(),
      source: {
        input_name: s.input.name,
        input_sha256: s.input.sha256,
        analyzed_at: s.analyzed_at,
        schema_version: index.doc.schema_version,
      },
      items: shortlistItems(index, reviews),
    },
    null,
    1,
  );
}

export function analysisJson(doc: AnalysisDocument): string {
  return JSON.stringify(doc);
}

export function exportName(doc: AnalysisDocument, kind: string, ext: string): string {
  const stem = doc.summary.input.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "-");
  return `framecue-${stem.slice(0, 40) || "analysis"}-${kind}.${ext}`;
}

export function download(name: string, text: string, type: string): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
