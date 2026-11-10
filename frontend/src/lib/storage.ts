import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import type { AnalysisDocument, ViewName } from "@/api/types";
import type { Filters, Reviews } from "./doc-index";
import { validateDocument } from "./validate";

/* ---------------------------------------------------------------------------------------
 * Browser-local persistence (IndexedDB only; never cookies, localStorage or URLs).
 *
 * Stores:
 *   analysis  "current" -> the last analyzed document + metadata
 *   review    <analysisKey> -> editor decisions, notes and UI position for that analysis
 *
 * Every failure is converted into a StorageError with a user-facing reason; callers keep the
 * analysis in memory and show the limitation instead of crashing.
 * ------------------------------------------------------------------------------------- */

export const DB_NAME = "promolens";
export const DB_VERSION = 1;
const OPEN_TIMEOUT_MS = 4000;

export interface AnalysisMeta {
  key: string; // analysis identity: input sha256 + analysed-at time
  fileName: string;
  origin: "upload" | "sample" | "import";
  savedAt: string;
  bytes: number;
}

export interface UiState {
  view: ViewName;
  selectedId: string | null;
  filters: Filters;
}

interface StoredAnalysis {
  meta: AnalysisMeta;
  doc: AnalysisDocument;
}

interface StoredReview {
  key: string;
  reviews: Reviews;
  ui: UiState | null;
  updatedAt: string;
}

interface PromoLensDB extends DBSchema {
  analysis: { key: string; value: StoredAnalysis };
  review: { key: string; value: StoredReview };
}

export type StorageProblem = "unavailable" | "quota" | "corrupt" | "error";

export class StorageError extends Error {
  constructor(
    public readonly problem: StorageProblem,
    message: string,
  ) {
    super(message);
  }
}

export function analysisKey(doc: AnalysisDocument): string {
  return `${doc.summary.input.sha256}:${doc.summary.analyzed_at}`;
}

let dbPromise: Promise<IDBPDatabase<PromoLensDB>> | null = null;

function toStorageError(error: unknown): StorageError {
  if (error instanceof StorageError) return error;
  const name = (error as { name?: string })?.name ?? "";
  if (name === "QuotaExceededError" || /quota/i.test(String((error as Error)?.message))) {
    return new StorageError("quota", "Browser storage is full, so this analysis is not saved.");
  }
  if (name === "InvalidStateError" || name === "SecurityError" || name === "UnknownError") {
    return new StorageError(
      "unavailable",
      "This browser is not allowing local storage (for example a private window).",
    );
  }
  return new StorageError("error", "Local storage failed; your work stays in this tab.");
}

function db(): Promise<IDBPDatabase<PromoLensDB>> {
  if (dbPromise) return dbPromise;
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new StorageError("unavailable", "This browser has no IndexedDB."));
  }
  const opening = openDB<PromoLensDB>(DB_NAME, DB_VERSION, {
    upgrade(database, oldVersion) {
      // v1: initial schema. Future versions migrate here, keyed on oldVersion.
      if (oldVersion < 1) {
        database.createObjectStore("analysis");
        database.createObjectStore("review", { keyPath: "key" });
      }
    },
    blocking() {
      dbPromise = null; // another tab upgraded the schema; reopen next time
    },
  });
  const timeout = new Promise<never>((_, reject) =>
    setTimeout(
      () => reject(new StorageError("unavailable", "Local storage did not respond.")),
      OPEN_TIMEOUT_MS,
    ),
  );
  dbPromise = Promise.race([opening, timeout]).catch((error) => {
    dbPromise = null;
    throw toStorageError(error);
  });
  return dbPromise;
}

export async function saveAnalysis(doc: AnalysisDocument, meta: Omit<AnalysisMeta, "savedAt" | "key" | "bytes">) {
  const full: AnalysisMeta = {
    ...meta,
    key: analysisKey(doc),
    savedAt: new Date().toISOString(),
    bytes: JSON.stringify(doc).length,
  };
  try {
    const d = await db();
    const tx = d.transaction(["analysis", "review"], "readwrite");
    await tx.objectStore("analysis").put({ meta: full, doc }, "current");
    // Drop reviews that belong to other analyses: only the current one is kept.
    const store = tx.objectStore("review");
    for (const key of await store.getAllKeys()) if (key !== full.key) await store.delete(key);
    await tx.done;
    return full;
  } catch (error) {
    throw toStorageError(error);
  }
}

export interface Restored {
  doc: AnalysisDocument;
  meta: AnalysisMeta;
  reviews: Reviews;
  ui: UiState | null;
}

/** Load the last analysis. Corrupt or outdated records are deleted and reported. */
export async function loadAnalysis(): Promise<Restored | null> {
  let d: IDBPDatabase<PromoLensDB>;
  try {
    d = await db();
  } catch (error) {
    throw toStorageError(error);
  }
  let stored: StoredAnalysis | undefined;
  try {
    stored = await d.get("analysis", "current");
  } catch (error) {
    throw toStorageError(error);
  }
  if (!stored) return null;
  const check = validateDocument(stored.doc);
  if (!check.ok || !stored.meta || stored.meta.key !== analysisKey(check.ok ? check.doc : stored.doc)) {
    await clearAll().catch(() => undefined);
    throw new StorageError(
      "corrupt",
      "The saved analysis could not be read (it may be from an older version) and was removed.",
    );
  }
  const review = await d.get("review", stored.meta.key).catch(() => undefined);
  return {
    doc: check.doc,
    meta: stored.meta,
    reviews: review?.reviews ?? {},
    ui: review?.ui ?? null,
  };
}

export async function saveReview(key: string, reviews: Reviews, ui: UiState | null): Promise<void> {
  try {
    const d = await db();
    await d.put("review", { key, reviews, ui, updatedAt: new Date().toISOString() });
  } catch (error) {
    throw toStorageError(error);
  }
}

export async function clearAll(): Promise<void> {
  try {
    const d = await db();
    const tx = d.transaction(["analysis", "review"], "readwrite");
    await Promise.all([tx.objectStore("analysis").clear(), tx.objectStore("review").clear(), tx.done]);
  } catch (error) {
    throw toStorageError(error);
  }
}

/** Test hook: forget the cached connection (e.g. after deleting the database). */
export function resetConnectionForTests(): void {
  dbPromise?.then((d) => d.close()).catch(() => undefined);
  dbPromise = null;
}
