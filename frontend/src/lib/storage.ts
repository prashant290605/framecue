import { openDB, type DBSchema, type IDBPDatabase } from "idb";
import type { AnalysisDocument, ViewName } from "@/api/types";
import type { Decision, Filters, Reviews } from "./doc-index";
import { validateDocument } from "./validate";

/* ---------------------------------------------------------------------------------------
 * Browser-local persistence (IndexedDB only; never cookies, localStorage or URLs).
 *
 * Stores (schema v2):
 *   analysis  <analysisKey> -> an analysed document + its metadata
 *   library   <analysisKey> -> metadata only, for listing saved analyses cheaply
 *   review    <analysisKey> -> editor decisions, notes and UI position for that analysis
 *   app       "last"        -> the analysis key to reopen after a refresh
 *
 * v1 kept a single analysis under the key "current"; upgrading moves it into the library
 * with its decisions intact. Work saved under an earlier database name on the same site is
 * copied into this database the first time it opens empty (see adoptEarlier).
 *
 * Every failure is converted into a StorageError with a user-facing reason; callers keep the
 * analysis in memory and show the limitation instead of crashing.
 * ------------------------------------------------------------------------------------- */

export const DB_NAME = "framecue";
export const DB_VERSION = 2;
const OPEN_TIMEOUT_MS = 4000;

export interface AnalysisMeta {
  key: string; // analysis identity: input sha256 + analysed-at time
  fileName: string; // display name; editable
  origin: "upload" | "sample" | "import";
  savedAt: string;
  bytes: number;
  /** sha256 over the uploaded files' contents (and bundle), for duplicate detection. */
  fingerprint?: string | null;
  openedAt?: string;
  episodes?: number;
  candidates?: number;
  aiReviewed?: number;
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

interface AppDB extends DBSchema {
  analysis: { key: string; value: StoredAnalysis };
  library: { key: string; value: AnalysisMeta };
  review: { key: string; value: StoredReview };
  app: { key: string; value: string };
}

const STORES = ["analysis", "library", "review", "app"] as const;

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

/** Listing fields derived from the document, so the library never loads documents. */
function withSummary(meta: AnalysisMeta, doc: AnalysisDocument | undefined): AnalysisMeta {
  return {
    ...meta,
    fingerprint: meta.fingerprint ?? null,
    openedAt: meta.openedAt ?? meta.savedAt,
    episodes: doc?.summary?.episodes ?? meta.episodes,
    candidates: doc?.summary?.candidates ?? meta.candidates,
    aiReviewed: doc?.coverage?.ai_evaluated ?? meta.aiReviewed,
  };
}

let dbPromise: Promise<IDBPDatabase<AppDB>> | null = null;

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

function db(): Promise<IDBPDatabase<AppDB>> {
  if (dbPromise) return dbPromise;
  if (typeof indexedDB === "undefined") {
    return Promise.reject(new StorageError("unavailable", "This browser has no IndexedDB."));
  }
  const opening = openDB<AppDB>(DB_NAME, DB_VERSION, {
    upgrade(database, oldVersion, _newVersion, tx) {
      if (oldVersion < 1) {
        database.createObjectStore("analysis");
        database.createObjectStore("review", { keyPath: "key" });
      }
      if (oldVersion < 2) {
        database.createObjectStore("library");
        database.createObjectStore("app");
        if (oldVersion >= 1) {
          // Move v1's single "current" analysis into the library; its review record is
          // already keyed by the analysis key, so decisions and notes carry over unchanged.
          const analysis = tx.objectStore("analysis");
          void analysis.get("current").then((stored) => {
            if (!stored?.meta?.key) return;
            const meta = withSummary(stored.meta, stored.doc);
            void analysis.put({ meta, doc: stored.doc }, meta.key);
            void analysis.delete("current");
            void tx.objectStore("library").put(meta, meta.key);
            void tx.objectStore("app").put(meta.key, "last");
          });
        }
      }
    },
    blocking() {
      dbPromise = null; // another tab upgraded the schema; reopen next time
    },
  }).then(async (database) => {
    await adoptEarlier(database).catch(() => undefined); // never blocks opening
    return database;
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

const ADOPTED = "adopted";

/** Copy saved work from an earlier database name on this site into this one, once, when
 *  this database starts empty. The earlier database is only read, never changed, so an
 *  interrupted copy loses nothing; the marker stops deleted analyses from coming back. */
async function adoptEarlier(target: IDBPDatabase<AppDB>): Promise<void> {
  if (typeof indexedDB.databases !== "function" || (await target.get("app", ADOPTED))) return;
  if ((await target.count("library")) > 0) {
    await target.put("app", "existing", ADOPTED);
    return;
  }
  const copied: string[] = [];
  const names = (await indexedDB.databases())
    .map((d) => d.name)
    .filter((n): n is string => Boolean(n) && n !== DB_NAME);
  for (const name of names) {
    const source = await openDB(name).catch(() => null);
    if (!source) continue;
    const stores = [...source.objectStoreNames];
    if (!stores.includes("analysis") || !stores.includes("review")) {
      source.close();
      continue;
    }
    const read = async (store: string) => {
      const os = source.transaction(store, "readonly").objectStore(store);
      const [keys, values] = await Promise.all([os.getAllKeys(), os.getAll()]);
      return keys.map((k, i) => [String(k), values[i]] as const);
    };
    const analyses = await read("analysis");
    const reviews = await read("review");
    const library = stores.includes("library") ? await read("library") : [];
    const app = stores.includes("app") ? await read("app") : [];
    source.close();
    const tx = target.transaction([...STORES], "readwrite");
    for (const [key, value] of analyses) {
      const stored = value as StoredAnalysis;
      if (!stored?.meta?.key || !stored.doc) continue;
      const meta = withSummary(stored.meta, stored.doc);
      void tx.objectStore("analysis").put({ meta, doc: stored.doc }, meta.key);
      if (key === "current") {
        // an earlier single-analysis database: list it and reopen it after a refresh
        void tx.objectStore("library").put(meta, meta.key);
        void tx.objectStore("app").put(meta.key, "last");
      }
    }
    for (const [key, value] of library) void tx.objectStore("library").put(value as AnalysisMeta, key);
    for (const [, value] of reviews) void tx.objectStore("review").put(value as StoredReview);
    for (const [key, value] of app) if (key !== ADOPTED && typeof value === "string") void tx.objectStore("app").put(value, key);
    await tx.done;
    copied.push(name);
  }
  await target.put("app", copied.join(",") || "none", ADOPTED);
}

async function run<T>(work: (d: IDBPDatabase<AppDB>) => Promise<T>): Promise<T> {
  try {
    return await work(await db());
  } catch (error) {
    throw toStorageError(error);
  }
}

/** Save a new analysis to the library and make it the one reopened after a refresh. */
export async function saveAnalysis(
  doc: AnalysisDocument,
  meta: { fileName: string; origin: AnalysisMeta["origin"]; fingerprint?: string | null },
): Promise<AnalysisMeta> {
  const now = new Date().toISOString();
  const full = withSummary(
    { ...meta, key: analysisKey(doc), savedAt: now, openedAt: now, bytes: JSON.stringify(doc).length },
    doc,
  );
  return run(async (d) => {
    const tx = d.transaction(["analysis", "library", "app"], "readwrite");
    await Promise.all([
      tx.objectStore("analysis").put({ meta: full, doc }, full.key),
      tx.objectStore("library").put(full, full.key),
      tx.objectStore("app").put(full.key, "last"),
      tx.done,
    ]);
    return full;
  });
}

export interface Restored {
  doc: AnalysisDocument;
  meta: AnalysisMeta;
  reviews: Reviews;
  ui: UiState | null;
}

/** Open a saved analysis (the last one by default). Corrupt records are removed and reported. */
export async function loadAnalysis(key?: string): Promise<Restored | null> {
  const d = await run(async (x) => x);
  const target = key ?? (await run((x) => x.get("app", "last")));
  if (!target) return null;
  const stored = await run((x) => x.get("analysis", target));
  if (!stored) {
    if (!key) await run((x) => x.delete("app", "last")).catch(() => undefined);
    return null;
  }
  const check = validateDocument(stored.doc);
  if (!check.ok || !stored.meta || target !== analysisKey(check.ok ? check.doc : stored.doc)) {
    await deleteAnalysis(target).catch(() => undefined);
    throw new StorageError(
      "corrupt",
      "A saved analysis could not be read (it may be from an older version) and was removed.",
    );
  }
  const listed = await d.get("library", target).catch(() => undefined);
  const meta = withSummary({ ...(listed ?? stored.meta), openedAt: new Date().toISOString() }, check.doc);
  await run(async (x) => {
    const tx = x.transaction(["library", "app"], "readwrite");
    await Promise.all([tx.objectStore("library").put(meta, target), tx.objectStore("app").put(target, "last"), tx.done]);
  }).catch(() => undefined); // reopening still works if the timestamp cannot be written
  const review = await d.get("review", target).catch(() => undefined);
  return { doc: check.doc, meta, reviews: review?.reviews ?? {}, ui: review?.ui ?? null };
}

/** Saved analyses, most recently opened first. */
export async function listAnalyses(): Promise<AnalysisMeta[]> {
  const all = await run((d) => d.getAll("library"));
  return all.sort((a, b) => (b.openedAt ?? b.savedAt).localeCompare(a.openedAt ?? a.savedAt));
}

export async function findByFingerprint(fingerprint: string): Promise<AnalysisMeta | undefined> {
  return (await listAnalyses()).find((m) => m.fingerprint === fingerprint);
}

export async function renameAnalysis(key: string, name: string): Promise<AnalysisMeta | undefined> {
  const fileName = name.trim().slice(0, 120);
  if (!fileName) return undefined;
  return run(async (d) => {
    const tx = d.transaction(["analysis", "library"], "readwrite");
    const [meta, stored] = await Promise.all([tx.objectStore("library").get(key), tx.objectStore("analysis").get(key)]);
    if (!meta) return undefined;
    const next = { ...meta, fileName };
    await Promise.all([
      tx.objectStore("library").put(next, key),
      stored ? tx.objectStore("analysis").put({ ...stored, meta: { ...stored.meta, fileName } }, key) : undefined,
      tx.done,
    ]);
    return next;
  });
}

/** Remove one analysis with its decisions and notes. */
export async function deleteAnalysis(key: string): Promise<void> {
  await run(async (d) => {
    const tx = d.transaction([...STORES], "readwrite");
    const last = await tx.objectStore("app").get("last");
    await Promise.all([
      tx.objectStore("analysis").delete(key),
      tx.objectStore("library").delete(key),
      tx.objectStore("review").delete(key),
      last === key ? tx.objectStore("app").delete("last") : undefined,
      tx.done,
    ]);
  });
}

export async function saveReview(key: string, reviews: Reviews, ui: UiState | null): Promise<void> {
  await run((d) => d.put("review", { key, reviews, ui, updatedAt: new Date().toISOString() }));
}

export async function clearAll(): Promise<void> {
  await run(async (d) => {
    const tx = d.transaction([...STORES], "readwrite");
    await Promise.all([...STORES.map((s) => tx.objectStore(s).clear()), tx.done]);
  });
}

/* Backup ------------------------------------------------------------------------------ */

export const BACKUP_FORMAT = "framecue-backup-v1";

export interface Backup {
  format: typeof BACKUP_FORMAT;
  exported_at: string;
  analyses: { meta: AnalysisMeta; doc: AnalysisDocument; reviews: Reviews }[];
}

export const isBackup = (value: unknown): value is { format: string; analyses: unknown } =>
  typeof value === "object" && value !== null && (value as { format?: unknown }).format === BACKUP_FORMAT;

/** Every saved analysis with its decisions and notes, for moving to another browser or site. */
export async function exportBackup(): Promise<Backup> {
  return run(async (d) => {
    const metas = await d.getAll("library");
    const analyses = [];
    for (const meta of metas) {
      const stored = await d.get("analysis", meta.key);
      if (!stored) continue;
      const review = await d.get("review", meta.key);
      analyses.push({ meta, doc: stored.doc, reviews: review?.reviews ?? {} });
    }
    return { format: BACKUP_FORMAT, exported_at: new Date().toISOString(), analyses };
  });
}

const DECISIONS = new Set<Decision | null>(["accepted", "later", "rejected", null]);

function cleanReviews(value: unknown): Reviews {
  const out: Reviews = {};
  if (typeof value !== "object" || value === null) return out;
  for (const [id, r] of Object.entries(value as Record<string, unknown>)) {
    const rec = r as { decision?: unknown; note?: unknown; updatedAt?: unknown };
    if (typeof rec !== "object" || rec === null || !DECISIONS.has((rec.decision ?? null) as Decision | null)) continue;
    out[id] = {
      decision: (rec.decision ?? null) as Decision | null,
      note: typeof rec.note === "string" ? rec.note.slice(0, 500) : "",
      updatedAt: typeof rec.updatedAt === "string" ? rec.updatedAt : new Date().toISOString(),
    };
  }
  return out;
}

/** Add a backup's analyses to the library. Analyses already saved here are left untouched. */
export async function importBackup(data: unknown): Promise<{ imported: number; skipped: number }> {
  const items = isBackup(data) && Array.isArray(data.analyses) ? data.analyses : [];
  let imported = 0;
  let skipped = 0;
  for (const item of items as { meta?: Partial<AnalysisMeta>; doc?: unknown; reviews?: unknown }[]) {
    const check = validateDocument(item?.doc);
    if (!check.ok) {
      skipped++;
      continue;
    }
    const key = analysisKey(check.doc);
    if (await run((d) => d.get("library", key))) {
      skipped++;
      continue;
    }
    const m = item.meta ?? {};
    const meta = withSummary(
      {
        key,
        fileName: typeof m.fileName === "string" && m.fileName.trim() ? m.fileName.slice(0, 120) : check.doc.summary.input.name,
        origin: m.origin === "upload" || m.origin === "sample" ? m.origin : "import",
        savedAt: typeof m.savedAt === "string" ? m.savedAt : new Date().toISOString(),
        openedAt: typeof m.openedAt === "string" ? m.openedAt : undefined,
        bytes: JSON.stringify(check.doc).length,
        fingerprint: typeof m.fingerprint === "string" ? m.fingerprint : null,
      },
      check.doc,
    );
    await run(async (d) => {
      const tx = d.transaction(["analysis", "library", "review"], "readwrite");
      await Promise.all([
        tx.objectStore("analysis").put({ meta, doc: check.doc }, key),
        tx.objectStore("library").put(meta, key),
        tx.objectStore("review").put({ key, reviews: cleanReviews(item.reviews), ui: null, updatedAt: new Date().toISOString() }),
        tx.done,
      ]);
    });
    imported++;
  }
  return { imported, skipped };
}

/** Test hook: forget the cached connection (e.g. after deleting the database). */
export function resetConnectionForTests(): void {
  dbPromise?.then((d) => d.close()).catch(() => undefined);
  dbPromise = null;
}
