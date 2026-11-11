import { openDB } from "idb";
import { describe, expect, it, vi } from "vitest";
import { sampleDoc } from "@/test/helpers";
import {
  analysisKey,
  clearAll,
  DB_NAME,
  deleteAnalysis,
  exportBackup,
  findByFingerprint,
  importBackup,
  listAnalyses,
  loadAnalysis,
  renameAnalysis,
  resetConnectionForTests,
  saveAnalysis,
  saveReview,
  StorageError,
} from "./storage";
import { DEFAULT_FILTERS } from "./doc-index";

describe("IndexedDB persistence", () => {
  it("saves and restores the analysis, decisions, notes and position", async () => {
    const doc = sampleDoc();
    const meta = await saveAnalysis(doc, { fileName: "harbor.zip", origin: "upload" });
    expect(meta.key).toBe(analysisKey(doc));
    const id = doc.candidates[2].candidate_id;
    await saveReview(meta.key, { [id]: { decision: "later", note: "check audio", updatedAt: "t" } }, {
      view: "coverage",
      selectedId: id,
      filters: { ...DEFAULT_FILTERS, episode: 2 },
    });
    resetConnectionForTests(); // simulate a page reload
    const restored = await loadAnalysis();
    expect(restored?.doc).toEqual(doc);
    expect(restored?.meta.fileName).toBe("harbor.zip");
    expect(restored?.reviews[id]).toMatchObject({ decision: "later", note: "check audio" });
    expect(restored?.ui).toMatchObject({ view: "coverage", selectedId: id });
    expect(restored?.ui?.filters.episode).toBe(2);
  });

  it("returns null when nothing is saved", async () => {
    expect(await loadAnalysis()).toBeNull();
  });

  it("keeps every analysis with its own decisions and reopens any of them", async () => {
    const first = sampleDoc();
    const a = await saveAnalysis(first, { fileName: "a", origin: "upload", fingerprint: "fp-a" });
    await saveReview(a.key, { x: { decision: "accepted", note: "", updatedAt: "t" } }, null);
    const second = sampleDoc();
    second.summary.analyzed_at = "2026-11-11T00:00:00+00:00";
    const b = await saveAnalysis(second, { fileName: "b", origin: "upload" });
    expect((await loadAnalysis())?.meta.key).toBe(b.key); // the last saved reopens after a refresh
    expect((await loadAnalysis())?.reviews).toEqual({});
    expect((await loadAnalysis(a.key))?.reviews.x.decision).toBe("accepted");
    const listed = await listAnalyses();
    expect(listed.map((m) => m.fileName)).toEqual(["a", "b"]); // most recently opened first
    expect(listed[0]).toMatchObject({ episodes: first.summary.episodes, candidates: first.summary.candidates, aiReviewed: 0 });
    expect((await findByFingerprint("fp-a"))?.key).toBe(a.key);
  });

  it("renames and deletes one analysis without touching the others", async () => {
    const first = sampleDoc();
    const a = await saveAnalysis(first, { fileName: "a", origin: "upload" });
    const second = sampleDoc();
    second.summary.analyzed_at = "2026-11-11T00:00:00+00:00";
    const b = await saveAnalysis(second, { fileName: "b", origin: "upload" });
    await renameAnalysis(a.key, "  Episode pack  ");
    expect((await loadAnalysis(a.key))?.meta.fileName).toBe("Episode pack");
    await saveReview(b.key, { y: { decision: "later", note: "n", updatedAt: "t" } }, null);
    await deleteAnalysis(b.key);
    expect((await listAnalyses()).map((m) => m.key)).toEqual([a.key]);
    expect(await loadAnalysis(b.key)).toBeNull();
  });

  it("moves a v1 saved analysis and its decisions into the library", async () => {
    const doc = sampleDoc();
    const key = analysisKey(doc);
    const v1 = await openDB(DB_NAME, 1, {
      upgrade(d) {
        d.createObjectStore("analysis");
        d.createObjectStore("review", { keyPath: "key" });
      },
    });
    const meta = { key, fileName: "old.zip", origin: "upload", savedAt: "2026-11-10T10:00:00Z", bytes: 1 };
    await v1.put("analysis", { meta, doc }, "current");
    await v1.put("review", { key, reviews: { z: { decision: "rejected", note: "kept", updatedAt: "t" } }, ui: null, updatedAt: "t" });
    v1.close();
    const restored = await loadAnalysis();
    expect(restored?.meta.fileName).toBe("old.zip");
    expect(restored?.reviews.z).toMatchObject({ decision: "rejected", note: "kept" });
    expect((await listAnalyses()).map((m) => m.key)).toEqual([key]);
  });

  it("adopts saved work from an earlier database name on the same site, once", async () => {
    const doc = sampleDoc();
    const key = analysisKey(doc);
    const earlier = await openDB("earlier-name", 2, {
      upgrade(d) {
        d.createObjectStore("analysis");
        d.createObjectStore("review", { keyPath: "key" });
        d.createObjectStore("library");
        d.createObjectStore("app");
      },
    });
    const meta = { key, fileName: "kept.zip", origin: "upload", savedAt: "2026-11-10T10:00:00Z", bytes: 1 };
    await earlier.put("analysis", { meta, doc }, key);
    await earlier.put("library", meta, key);
    await earlier.put("app", key, "last");
    await earlier.put("review", { key, reviews: { z: { decision: "later", note: "kept", updatedAt: "t" } }, ui: null, updatedAt: "t" });
    earlier.close();
    const restored = await loadAnalysis();
    expect(restored?.meta.fileName).toBe("kept.zip");
    expect(restored?.reviews.z).toMatchObject({ decision: "later", note: "kept" });
    // the earlier database is left untouched, and the copy happens only once
    expect((await indexedDB.databases()).map((d) => d.name)).toContain("earlier-name");
    await deleteAnalysis(key);
    resetConnectionForTests(); // reload
    expect(await listAnalyses()).toEqual([]);
  });

  it("adopts a single-analysis (v1) database saved under an earlier name", async () => {
    const doc = sampleDoc();
    const key = analysisKey(doc);
    const earlier = await openDB("earlier-v1", 1, {
      upgrade(d) {
        d.createObjectStore("analysis");
        d.createObjectStore("review", { keyPath: "key" });
      },
    });
    await earlier.put("analysis", { meta: { key, fileName: "one.zip", origin: "upload", savedAt: "t", bytes: 1 }, doc }, "current");
    earlier.close();
    expect((await loadAnalysis())?.meta.fileName).toBe("one.zip");
    expect((await listAnalyses()).map((m) => m.key)).toEqual([key]);
  });

  it("removes and reports corrupt or outdated data", async () => {
    const database = await openDB(DB_NAME, 1, {
      upgrade(d) {
        d.createObjectStore("analysis");
        d.createObjectStore("review", { keyPath: "key" });
      },
    });
    await database.put("analysis", { meta: { key: "k" }, doc: { schema_version: "old" } }, "current");
    database.close();
    await expect(loadAnalysis()).rejects.toMatchObject({ problem: "corrupt" });
    resetConnectionForTests();
    expect(await loadAnalysis()).toBeNull(); // cleaned up, so the next start is clean
    expect(await listAnalyses()).toEqual([]);
  });

  it("backs up every analysis with decisions and restores them elsewhere", async () => {
    const doc = sampleDoc();
    const a = await saveAnalysis(doc, { fileName: "a", origin: "upload" });
    await saveReview(a.key, { x: { decision: "accepted", note: "lead", updatedAt: "t" } }, null);
    const backup = JSON.parse(JSON.stringify(await exportBackup()));
    await clearAll(); // e.g. a different site or browser
    expect(await importBackup(backup)).toEqual({ imported: 1, skipped: 0 });
    expect(await importBackup(backup)).toEqual({ imported: 0, skipped: 1 }); // already here
    expect((await loadAnalysis(a.key))?.reviews.x).toMatchObject({ decision: "accepted", note: "lead" });
    expect(await importBackup({ format: "framecue-backup-v1", analyses: [{ doc: { bad: true } }] })).toEqual({ imported: 0, skipped: 1 });
  });

  it("reports unavailable storage without throwing anything else", async () => {
    vi.stubGlobal("indexedDB", undefined);
    resetConnectionForTests();
    await expect(saveAnalysis(sampleDoc(), { fileName: "a", origin: "upload" })).rejects.toBeInstanceOf(StorageError);
    await expect(loadAnalysis()).rejects.toMatchObject({ problem: "unavailable" });
  });

  it("maps quota errors to a clear message", async () => {
    await saveAnalysis(sampleDoc(), { fileName: "a", origin: "upload" });
    const quota = Object.assign(new Error("QuotaExceededError"), { name: "QuotaExceededError" });
    const spy = vi.spyOn(IDBObjectStore.prototype, "put").mockImplementation(() => {
      throw quota;
    });
    const error = await saveReview("k", {}, null).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(StorageError);
    expect((error as StorageError).problem).toBe("quota");
    spy.mockRestore();
  });

  it("deletes everything on request", async () => {
    await saveAnalysis(sampleDoc(), { fileName: "a", origin: "upload" });
    await clearAll();
    expect(await loadAnalysis()).toBeNull();
  });
});
