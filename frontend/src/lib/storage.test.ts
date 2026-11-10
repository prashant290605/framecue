import { openDB } from "idb";
import { describe, expect, it, vi } from "vitest";
import { sampleDoc } from "@/test/helpers";
import {
  analysisKey,
  clearAll,
  DB_NAME,
  DB_VERSION,
  loadAnalysis,
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

  it("drops reviews that belong to a previous analysis", async () => {
    const first = sampleDoc();
    const meta = await saveAnalysis(first, { fileName: "a", origin: "upload" });
    await saveReview(meta.key, { x: { decision: "accepted", note: "", updatedAt: "t" } }, null);
    const second = sampleDoc();
    second.summary.analyzed_at = "2026-11-11T00:00:00+00:00";
    await saveAnalysis(second, { fileName: "b", origin: "upload" });
    expect((await loadAnalysis())?.reviews).toEqual({});
  });

  it("removes and reports corrupt or outdated data", async () => {
    const database = await openDB(DB_NAME, DB_VERSION, {
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
