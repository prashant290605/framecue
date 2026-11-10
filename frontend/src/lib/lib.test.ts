import { describe, expect, it } from "vitest";
import { sampleDoc, sampleDocWithAi } from "@/test/helpers";
import {
  buildIndex,
  buildTimeline,
  cutFamily,
  DEFAULT_FILTERS,
  filterEntries,
  intersects,
  type Reviews,
} from "./doc-index";
import { cell, RANKED_COLUMNS, rankedCsv, rankedRows, shortlistCsv, shortlistJson } from "./exporters";
import { bytes, seconds, timecode } from "./time";
import { validateDocument } from "./validate";

describe("time formatting", () => {
  it("keeps centisecond precision and matches the backend format", () => {
    expect(timecode(0)).toBe("00:00.00");
    expect(timecode(57_800)).toBe("00:57.80");
    expect(timecode(96_090)).toBe("01:36.09");
    expect(timecode(12_345)).toBe("00:12.345"); // not a whole centisecond: 3 digits
    expect(seconds(38_290)).toBe("38.29 s");
    expect(bytes(4_000_000)).toBe("4.0 MB");
    expect(bytes(540_294)).toBe("540 KB");
  });
});

describe("validateDocument", () => {
  it("accepts real API output", () => {
    expect(validateDocument(sampleDoc()).ok).toBe(true);
    expect(validateDocument(sampleDocWithAi()).ok).toBe(true);
  });

  it.each([
    ["wrong schema", (d: any) => (d.schema_version = "v0")],
    ["unknown view member", (d: any) => d.views.ai.order.push("C999-0000000-0000001")],
    ["AI score out of range", (d: any) => (Object.values(d.ai_evaluations)[0] as any).weighted_score = 140],
    ["evidence to unknown event", (d: any) => ((Object.values(d.ai_evaluations)[0] as any).hook_event_ids = ["E999-S01-0001"])],
    ["count mismatch", (d: any) => d.candidates.pop()],
    ["inverted clip", (d: any) => (d.candidates[0].end_ms = d.candidates[0].start_ms)],
    ["bad event range", (d: any) => (d.episodes[0].event_range = [5, 1])],
  ])("rejects %s", (_, mutate) => {
    const doc = sampleDocWithAi() as any;
    mutate(doc);
    expect(validateDocument(doc).ok).toBe(false);
  });

  it("rejects non-objects", () => {
    expect(validateDocument(null).ok).toBe(false);
    expect(validateDocument([1, 2]).ok).toBe(false);
  });
});

describe("index and filters", () => {
  const index = buildIndex(sampleDocWithAi());

  it("keeps the two views separate and ranks primaries in order", () => {
    const ai = index.entries.ai;
    expect(new Set(ai.map((e) => e.id))).toEqual(new Set(Object.keys(index.ai)));
    expect(index.entries.coverage).toHaveLength(index.doc.candidates.length);
    const ranks = filterEntries(index, "coverage", DEFAULT_FILTERS, "", {}).map((e) => e.rank);
    expect(ranks).toEqual(ranks.map((_, i) => i + 1));
    const aiScores = filterEntries(index, "ai", DEFAULT_FILTERS, "", {}).map((e) => index.ai[e.id].weighted_score);
    expect(aiScores).toEqual([...aiScores].sort((a, b) => b - a));
  });

  it("filters by episode, duration, status, decision and alternatives", () => {
    const f = { ...DEFAULT_FILTERS, alternatives: true };
    const all = filterEntries(index, "coverage", f, "", {});
    expect(all).toHaveLength(index.doc.candidates.length);
    const ep2 = filterEntries(index, "coverage", { ...f, episode: 2 }, "", {});
    expect(ep2.length).toBeGreaterThan(0);
    expect(ep2.every((e) => index.candidates.get(e.id)!.episode_number === 2)).toBe(true);
    const short = filterEntries(index, "coverage", { ...f, duration: "short" }, "", {});
    expect(short.every((e) => index.candidates.get(e.id)!.duration_ms <= 30_000)).toBe(true);
    const reviewed = filterEntries(index, "coverage", { ...f, status: "ai_evaluated" }, "", {});
    expect(reviewed.map((e) => e.id).sort()).toEqual(Object.keys(index.ai).sort());
    const id = all[3].id;
    const reviews: Reviews = { [id]: { decision: "accepted", note: "", updatedAt: "t" } };
    expect(filterEntries(index, "coverage", { ...f, decision: "accepted" }, "", reviews).map((e) => e.id)).toEqual([id]);
    expect(filterEntries(index, "coverage", { ...f, decision: "undecided" }, "", reviews)).toHaveLength(all.length - 1);
    const primaries = filterEntries(index, "coverage", DEFAULT_FILTERS, "", {});
    expect(primaries.every((e) => e.rank !== null)).toBe(true);
  });

  it("searches clip text, speakers and ids", () => {
    const f = { ...DEFAULT_FILTERS, alternatives: true };
    const hits = filterEntries(index, "coverage", f, "seabird", {});
    expect(hits.length).toBeGreaterThan(0);
    for (const e of hits) expect(index.searchText(e.id)).toContain("seabird");
    const byId = filterEntries(index, "coverage", f, index.doc.candidates[0].candidate_id.toLowerCase(), {});
    expect(byId.map((e) => e.id)).toContain(index.doc.candidates[0].candidate_id);
    expect(filterEntries(index, "coverage", f, "zzzz-no-such-text", {})).toHaveLength(0);
  });

  it("groups cut families", () => {
    const alt = index.entries.coverage.find((e) => e.alternativeOf);
    expect(alt).toBeDefined();
    const fam = cutFamily(index, "coverage", alt!.id);
    expect(fam[0]).toBe(alt!.alternativeOf);
    expect(fam).toContain(alt!.id);
  });
});

describe("timeline", () => {
  const index = buildIndex(sampleDoc());
  const c = index.doc.candidates.find((x) => x.start_ms > 15_000)!;
  const model = buildTimeline(index, c);

  it("uses exact clip, hook (3 s) and ending (5 s) boundaries with ±10 s context", () => {
    expect(model.clip).toEqual([c.start_ms, c.end_ms]);
    expect(model.hook).toEqual([c.start_ms, c.start_ms + 3000]);
    expect(model.ending).toEqual([c.end_ms - 5000, c.end_ms]);
    expect(model.lo).toBe(c.start_ms - 10_000);
    const ep = index.episodes.get(c.episode_number)!;
    expect(model.hi).toBe(Math.min(ep.duration_ms, c.end_ms + 10_000));
  });

  it("classifies events with half-open intervals and packs overlaps into rows", () => {
    for (const p of model.events) {
      expect(p.inClip).toBe(intersects(p.event, c.start_ms, c.end_ms));
      expect(intersects(p.event, model.lo, model.hi)).toBe(true);
    }
    for (const lane of model.lanes) {
      const byRow = new Map<number, typeof lane.events>();
      for (const p of lane.events) byRow.set(p.row, [...(byRow.get(p.row) ?? []), p]);
      for (const row of byRow.values()) {
        for (let i = 1; i < row.length; i++) expect(row[i].event.start_ms).toBeGreaterThanOrEqual(row[i - 1].event.end_ms);
      }
    }
    // the camera lane overlaps dialogue in time but never shares a lane with it
    expect(model.lanes.find((l) => l.lane === "CAMERA")!.events.every((p) => p.event.type === "CAMERA")).toBe(true);
  });

  it("treats zero-length events as instants", () => {
    expect(intersects({ start_ms: 100, end_ms: 100 }, 100, 200)).toBe(true);
    expect(intersects({ start_ms: 200, end_ms: 200 }, 100, 200)).toBe(false);
    expect(intersects({ start_ms: 0, end_ms: 100 }, 100, 200)).toBe(false);
  });
});

describe("exports", () => {
  const index = buildIndex(sampleDocWithAi());

  it("writes the AI view with exact values and provenance", () => {
    const entries = filterEntries(index, "ai", { ...DEFAULT_FILTERS, alternatives: true }, "", {});
    const csv = rankedCsv(index, "ai", entries, {});
    const [head, first] = csv.trim().split("\n");
    expect(head.split(",")).toEqual([...RANKED_COLUMNS]);
    const row = rankedRows(index, "ai", entries, {})[0];
    const j = index.ai[row.candidate_id];
    const c = index.candidates.get(row.candidate_id)!;
    expect(row.score).toBe(j.weighted_score);
    expect(row.score_system).toBe("ai_weighted_v1");
    expect(row.start_ms).toBe(c.start_ms);
    expect(row.start_timestamp).toBe(c.start_timestamp);
    expect(row.hook_event_ids).toEqual(j.hook_event_ids);
    expect(first.startsWith(`ai,1,,${c.candidate_id},${c.episode_number},${c.start_timestamp}`)).toBe(true);
  });

  it("never puts AI fields on full-show rows", () => {
    const entries = filterEntries(index, "coverage", { ...DEFAULT_FILTERS, alternatives: true }, "", {});
    const rows = rankedRows(index, "coverage", entries, {});
    expect(rows).toHaveLength(index.doc.candidates.length);
    for (const r of rows) {
      expect(r.score_system).toBe("heuristic_priority_v1");
      expect(r.score).toBe(index.candidates.get(r.candidate_id)!.baseline.priority);
      expect(r.hook_explanation).toBeUndefined();
      expect(r.hook_score).toBeUndefined();
    }
  });

  it("quotes CSV cells and neutralises spreadsheet formulas", () => {
    expect(cell('He said "no", then left')).toBe('"He said ""no"", then left"');
    expect(cell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(cell(["a", "b"])).toBe("a;b");
    expect(cell(null)).toBe("");
    expect(cell(false)).toBe("false");
  });

  it("exports the editor shortlist with decisions, notes and both score systems", () => {
    const [a, b] = index.doc.candidates;
    const reviews: Reviews = {
      [a.candidate_id]: { decision: "accepted", note: "Lead promo, check music", updatedAt: "t" },
      [b.candidate_id]: { decision: null, note: "maybe", updatedAt: "t" },
    };
    const json = JSON.parse(shortlistJson(index, reviews, new Date("2026-11-10T00:00:00Z")));
    expect(json.format).toBe("promolens-shortlist-v1");
    expect(json.source.input_sha256).toBe(index.doc.summary.input.sha256);
    expect(json.items).toHaveLength(2);
    const item = json.items.find((i: any) => i.candidate_id === a.candidate_id);
    expect(item.editor_decision).toBe("accepted");
    expect(item.heuristic_priority).toBe(a.baseline.priority);
    expect(item.heuristic_score_system).toBe("heuristic_priority_v1");
    expect(item.ai_score).toBe(index.ai[a.candidate_id]?.weighted_score ?? null);
    const csv = shortlistCsv(index, reviews);
    expect(csv.split("\n")).toHaveLength(4); // header, 2 rows, trailing newline
    expect(csv).toContain("Lead promo, check music");
  });
});
