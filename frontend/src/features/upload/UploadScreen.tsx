import { ArrowRight, BookOpen, CircleAlert, FileArchive, FileJson, FileText, FolderOpen, Loader2, ShieldCheck, Upload, X } from "lucide-react";
import * as React from "react";
import { analyze, ApiError, CLI_HINT, DEFAULT_MAX_UPLOAD, getHealth } from "@/api/client";
import { Brand } from "@/components/Brand";
import { Button } from "@/components/ui/button";
import { fingerprint } from "@/lib/fingerprint";
import { findByFingerprint, importBackup, isBackup, listAnalyses, type AnalysisMeta } from "@/lib/storage";
import { bytes } from "@/lib/time";
import { validateDocument } from "@/lib/validate";
import { cn } from "@/lib/utils";
import { useApp } from "@/state/app-state";
import { RecentAnalyses } from "./RecentAnalyses";

export const SAMPLE_FILES = ["harbor-light-01.txt", "harbor-light-02.txt", "harbor-light-03.txt"];
/** Genuine Groq evaluations of the fictional sample only, made offline with the CLI and
 *  re-validated by the server like any uploaded bundle. Optional: the sample still works
 *  without it. */
export const SAMPLE_BUNDLE = "harbor-light.ai-bundle.json";
/** Always published; names the bundle once one exists (`ai_bundle: null` until then), so the
 *  page never requests a missing file. */
export const SAMPLE_MANIFEST = "manifest.json";

const isJson = (r: Response) => r.ok && (r.headers.get("content-type") ?? "").includes("json");

async function fetchSampleBundle(): Promise<File | null> {
  try {
    const m = await fetch(`/sample/${SAMPLE_MANIFEST}`);
    // the SPA rewrite answers missing files with index.html, so insist on JSON
    if (!isJson(m) || ((await m.json()) as { ai_bundle?: unknown }).ai_bundle !== SAMPLE_BUNDLE) return null;
    const r = await fetch(`/sample/${SAMPLE_BUNDLE}`);
    if (!isJson(r)) return null;
    return new File([await r.blob()], SAMPLE_BUNDLE, { type: "application/json" });
  } catch {
    return null;
  }
}

/** Start downloading the workspace code while the analysis request is in flight. */
const prefetchWorkspace = () => void import("@/features/workspace/Workspace").catch(() => undefined);

type Problem = { title: string; detail?: string } | null;
type Duplicate = { meta: AnalysisMeta; files: File[]; bundle: File | null; name: string } | null;

export function checkSelection(files: File[], bundle: File | null, maxBytes: number): Problem {
  if (files.length === 0) return { title: "Choose a screenplay file first." };
  const bad = files.find((f) => !/\.(zip|txt)$/i.test(f.name));
  if (bad) return { title: `“${bad.name}” is not a supported file.`, detail: "Upload one .zip pack or one or more .txt episodes." };
  const zips = files.filter((f) => /\.zip$/i.test(f.name));
  if (zips.length && files.length > 1) return { title: "Upload either one .zip pack or .txt episodes, not both." };
  if (bundle && !/\.json$/i.test(bundle.name)) return { title: "The AI bundle must be a .json file." };
  const total = files.reduce((n, f) => n + f.size, 0) + (bundle?.size ?? 0);
  if (total > maxBytes) {
    return { title: `This upload is ${bytes(total)}; the online limit is ${bytes(maxBytes)}.`, detail: CLI_HINT };
  }
  return null;
}

export function UploadScreen() {
  const { load, open, dispatch } = useApp();
  const [files, setFiles] = React.useState<File[]>([]);
  const [bundle, setBundle] = React.useState<File | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [busy, setBusy] = React.useState<null | "upload" | "sample">(null);
  const [elapsed, setElapsed] = React.useState(0);
  const [problem, setProblem] = React.useState<Problem>(null);
  const [duplicate, setDuplicate] = React.useState<Duplicate>(null);
  const [library, setLibrary] = React.useState<AnalysisMeta[]>([]);
  const [maxBytes, setMaxBytes] = React.useState(DEFAULT_MAX_UPLOAD);
  const [service, setService] = React.useState<"checking" | "ok" | "down">("checking");
  const controller = React.useRef<AbortController | null>(null);
  const fileInput = React.useRef<HTMLInputElement>(null);
  const bundleInput = React.useRef<HTMLInputElement>(null);
  const importInput = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    const c = new AbortController();
    getHealth(c.signal)
      .then((h) => {
        setService("ok");
        if (h.max_upload_bytes) setMaxBytes(h.max_upload_bytes);
      })
      .catch(() => !c.signal.aborted && setService("down"));
    return () => c.abort();
  }, []);

  React.useEffect(() => {
    if (!busy) return;
    const started = performance.now();
    const t = setInterval(() => setElapsed((performance.now() - started) / 1000), 100);
    return () => clearInterval(t);
  }, [busy]);

  React.useEffect(() => () => controller.current?.abort(), []);

  const refresh = React.useCallback(() => {
    listAnalyses().then(setLibrary).catch(() => setLibrary([]));
  }, []);
  React.useEffect(refresh, [refresh]);

  const openSaved = async (key: string) => {
    prefetchWorkspace();
    history.pushState(null, "", "/review");
    return open(key);
  };

  const run = async (chosen: File[], chosenBundle: File | null, origin: "upload" | "sample", name: string, again = false) => {
    const issue = checkSelection(chosen, chosenBundle, maxBytes);
    if (issue) {
      setProblem(issue);
      return;
    }
    setProblem(null);
    setDuplicate(null);
    // Same files analysed before? Reopen the saved analysis (with its decisions) instead.
    const print = await fingerprint(chosen, chosenBundle);
    if (print && !again) {
      const saved = await findByFingerprint(print).catch(() => undefined);
      if (saved && origin === "sample" && (await openSaved(saved.key))) return;
      if (saved && origin === "upload") {
        setDuplicate({ meta: saved, files: chosen, bundle: chosenBundle, name });
        return;
      }
    }
    setBusy(origin);
    setElapsed(0);
    prefetchWorkspace();
    controller.current = new AbortController();
    try {
      const doc = await analyze({ files: chosen, bundle: chosenBundle, signal: controller.current.signal });
      history.pushState(null, "", "/review");
      load(doc, name, origin, print);
    } catch (error) {
      if (error instanceof ApiError && error.code === "cancelled") setProblem({ title: "Analysis cancelled." });
      else setProblem({ title: error instanceof ApiError ? error.message : "Something went wrong. Please try again." });
    } finally {
      setBusy(null);
    }
  };

  const onAnalyze = () => {
    const name = files.length === 1 ? files[0].name : `${files.length} episode files`;
    void run(files, bundle, "upload", name);
  };

  const onSample = async () => {
    setProblem(null);
    setBusy("sample");
    try {
      const [sample, sampleBundle] = await Promise.all([
        Promise.all(
          SAMPLE_FILES.map(async (name) => {
            const r = await fetch(`/sample/${name}`);
            if (!r.ok) throw new Error("sample missing");
            return new File([await r.blob()], name, { type: "text/plain" });
          }),
        ),
        fetchSampleBundle(),
      ]);
      setBusy(null);
      await run(sample, sampleBundle, "sample", "The Harbor Light (fictional sample)");
    } catch {
      setBusy(null);
      setProblem({ title: "The sample could not be loaded." });
    }
  };

  const onImport = async (file: File | undefined) => {
    if (!file) return;
    prefetchWorkspace();
    try {
      const data: unknown = JSON.parse(await file.text());
      if (isBackup(data)) {
        const { imported, skipped } = await importBackup(data);
        refresh();
        const extra = skipped ? ` ${skipped} skipped (already here or unreadable).` : "";
        const noun = imported === 1 ? "analysis" : "analyses";
        dispatch({ type: "notice", tone: "info", text: `${imported} ${noun} imported.${extra}` });
        return;
      }
      const check = validateDocument(data);
      if (!check.ok) {
        setProblem({ title: "That file is not a FrameCue analysis.", detail: check.reason });
        return;
      }
      history.pushState(null, "", "/review");
      load(check.doc, check.doc.summary.input.name, "import");
      dispatch({ type: "notice", tone: "info", text: "Analysis opened from file. Nothing was uploaded." });
    } catch (error) {
      setProblem({ title: error instanceof SyntaxError ? "That file is not valid JSON." : "The file could not be imported." });
    }
  };

  const accept = (list: FileList | null) => {
    if (!list?.length) return;
    const chosen = [...list];
    setFiles(chosen);
    setProblem(checkSelection(chosen, bundle, maxBytes));
  };

  const total = files.reduce((n, f) => n + f.size, 0) + (bundle?.size ?? 0);

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex h-16 max-w-[1120px] items-center justify-between gap-4 px-5 sm:px-8">
          <Brand />
          <div className="flex items-center gap-1 sm:gap-3">
            <span className="hidden items-center gap-2 text-[12px] font-medium text-muted sm:flex" aria-live="polite">
              <span
                className={cn("size-1.5 rounded-full", service === "ok" ? "bg-ok" : service === "down" ? "bg-no" : "bg-faint")}
                aria-hidden
              />
              {service === "ok" ? "Analysis service online" : service === "down" ? "Service unreachable" : "Checking service"}
            </span>
            <span className="hidden h-5 w-px bg-line sm:block" aria-hidden />
            <Button variant="ghost" size="sm" onClick={() => importInput.current?.click()} aria-label="Import analysis or backup">
              <FolderOpen /> Import
            </Button>
            <input
              ref={importInput}
              type="file"
              accept=".json,application/json"
              className="sr-only"
              aria-label="Analysis or backup file"
              data-testid="import-input"
              onChange={(e) => {
                void onImport(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
          </div>
        </div>
      </header>

      <main className="flex-1 px-5 sm:px-8">
        <div className="mx-auto w-full max-w-[640px] pt-12 pb-16 sm:pt-16 lg:pt-20">
          <p className="text-[13px] font-semibold text-accent">Promo discovery for microdrama</p>
          <h1 className="mt-3 text-[34px] leading-[1.1] font-bold tracking-[-0.035em] text-balance text-ink sm:text-[44px]">
            Find the moments worth promoting.
          </h1>
          <p className="mt-4 max-w-[520px] text-[15px] leading-relaxed text-muted sm:text-[16px]">
            Upload timestamped screenplays and get ranked 20–60 second promo cuts with exact timecodes,
            ready for editorial review.
          </p>

          <section aria-labelledby="analyze-heading" className="mt-10 rounded-xl border border-line bg-surface">
            <h2 id="analyze-heading" className="sr-only">Analyze a screenplay</h2>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                accept(e.dataTransfer.files);
              }}
              className={cn(
                "m-2 flex flex-col items-center rounded-lg border border-dashed px-6 py-9 text-center transition-colors sm:py-10",
                dragging ? "border-accent bg-accent-wash" : "border-line-strong bg-canvas/60",
              )}
            >
              <span
                className={cn(
                  "flex size-11 items-center justify-center rounded-full transition-colors",
                  dragging ? "bg-accent text-white" : "bg-surface text-accent ring-1 ring-line",
                )}
              >
                <Upload className="size-5" aria-hidden />
              </span>
              <p className="mt-4 text-[15px] font-semibold text-ink">Drop screenplay files here</p>
              <p className="mt-1 text-[13px] text-muted">One .zip pack or .txt episodes · up to {bytes(maxBytes)}</p>
              <Button size="sm" className="mt-5" onClick={() => fileInput.current?.click()}>
                Choose files
              </Button>
              <input
                ref={fileInput}
                type="file"
                accept=".zip,.txt"
                multiple
                className="sr-only"
                aria-label="Screenplay files"
                data-testid="screenplay-input"
                onChange={(e) => {
                  accept(e.target.files);
                  e.target.value = "";
                }}
              />
            </div>

            {files.length > 0 && (
              <div className="px-5 pt-2 pb-3">
                <div className="flex items-center justify-between">
                  <p className="text-[12px] font-semibold text-muted">
                    {files.length === 1 ? "1 file selected" : `${files.length} files selected`}
                  </p>
                  <button
                    type="button"
                    className="rounded text-[12px] font-semibold text-muted hover:text-ink"
                    onClick={() => {
                      setFiles([]);
                      setProblem(null);
                    }}
                  >
                    Clear
                  </button>
                </div>
                <ul className="mt-1.5 divide-y divide-line" aria-label="Selected files">
                  {files.slice(0, 4).map((f) => (
                    <li key={f.name} className="flex items-center gap-2.5 py-2 text-[13px] text-ink">
                      {/\.zip$/i.test(f.name) ? <FileArchive className="size-4 text-muted" /> : <FileText className="size-4 text-muted" />}
                      <span className="min-w-0 flex-1 truncate font-medium">{f.name}</span>
                      <span className="tabular text-[12px] text-muted">{bytes(f.size)}</span>
                    </li>
                  ))}
                  {files.length > 4 && <li className="py-2 text-[12px] text-muted">and {files.length - 4} more</li>}
                </ul>
              </div>
            )}

            <div className="flex flex-col items-start gap-3 border-t border-line px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
              <div className="min-w-0">
                <p className="text-[13px] font-semibold text-ink">
                  AI evaluation bundle <span className="ml-1 font-medium text-muted">Optional</span>
                </p>
                <p className="mt-0.5 text-[12px] break-all text-muted sm:truncate">
                  {bundle ? `${bundle.name} · ${bytes(bundle.size)}` : "Adds AI reviews generated offline with the CLI."}
                </p>
              </div>
              {bundle ? (
                <Button size="sm" variant="ghost" onClick={() => setBundle(null)} aria-label="Remove AI bundle">
                  <X /> Remove
                </Button>
              ) : (
                <Button size="sm" onClick={() => bundleInput.current?.click()}>
                  <FileJson /> Add bundle
                </Button>
              )}
              <input
                ref={bundleInput}
                type="file"
                accept=".json,application/json"
                className="sr-only"
                aria-label="AI evaluation bundle"
                data-testid="bundle-input"
                onChange={(e) => {
                  const f = e.target.files?.[0] ?? null;
                  setBundle(f);
                  setProblem(f ? checkSelection(files, f, maxBytes) : null);
                  e.target.value = "";
                }}
              />
            </div>

            {duplicate && (
              <div aria-live="polite" className="mx-5 mb-4 flex flex-col gap-2.5 rounded-md bg-accent-wash px-3.5 py-3 text-[13px] sm:flex-row sm:items-center sm:justify-between">
                <p className="min-w-0 text-ink-soft">
                  Already analyzed as <span className="font-semibold text-ink">{duplicate.meta.fileName}</span>.
                </p>
                <div className="flex shrink-0 gap-2">
                  <Button size="sm" variant="primary" onClick={() => void openSaved(duplicate.meta.key)}>
                    Open saved
                  </Button>
                  <Button size="sm" onClick={() => void run(duplicate.files, duplicate.bundle, "upload", duplicate.name, true)}>
                    Analyze again
                  </Button>
                </div>
              </div>
            )}

            {problem && (
              <div role="alert" className="mx-5 mb-4 flex gap-2.5 rounded-md bg-no-soft px-3.5 py-3 text-[13px] text-no">
                <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
                <div className="min-w-0">
                  <p className="font-semibold">{problem.title}</p>
                  {problem.detail && <p className="mt-0.5 text-no/85">{problem.detail}</p>}
                </div>
              </div>
            )}

            <div className="border-t border-line p-4 sm:px-5">
              {busy ? (
                <div className="flex h-11 items-center justify-between gap-3" aria-live="polite">
                  <span className="flex min-w-0 items-center gap-2.5 text-[13px] font-semibold text-ink">
                    <Loader2 className="size-4 shrink-0 animate-spin text-accent" aria-hidden />
                    {busy === "sample" ? "Analyzing the sample" : "Analyzing"}
                    <span className="font-medium tabular text-muted">{elapsed.toFixed(1)} s</span>
                    {elapsed > 8 && <span className="truncate font-medium text-muted max-sm:hidden">Large packs take a few seconds</span>}
                  </span>
                  <Button size="sm" variant="ghost" onClick={() => controller.current?.abort()}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <div className="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <Button variant="ghost" size="lg" className="px-3 sm:-ml-1" onClick={onSample}>
                    <BookOpen className="text-muted" /> Try the fictional sample
                  </Button>
                  <Button variant="primary" size="lg" className="sm:min-w-40" onClick={onAnalyze} disabled={files.length === 0}>
                    Analyze{total ? ` · ${bytes(total)}` : ""} <ArrowRight />
                  </Button>
                </div>
              )}
            </div>
          </section>

          <p className="mt-4 flex items-center gap-2 text-[12px] text-muted">
            <ShieldCheck className="size-3.5 shrink-0 text-faint" aria-hidden />
            Processed in memory with no AI model called. Results stay in this browser.
          </p>

          <RecentAnalyses items={library} onChange={refresh} />
        </div>
      </main>
    </div>
  );
}
