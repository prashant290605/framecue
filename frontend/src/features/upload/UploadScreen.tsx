import { FileArchive, FileJson, FileText, FolderOpen, Info, Loader2, ShieldCheck, Sparkles, Upload, X } from "lucide-react";
import * as React from "react";
import { analyze, ApiError, CLI_HINT, DEFAULT_MAX_UPLOAD, getHealth } from "@/api/client";
import { Brand } from "@/components/Brand";
import { Button } from "@/components/ui/button";
import { bytes } from "@/lib/time";
import { validateDocument } from "@/lib/validate";
import { cn } from "@/lib/utils";
import { useApp } from "@/state/app-state";

export const SAMPLE_FILES = ["harbor-light-01.txt", "harbor-light-02.txt", "harbor-light-03.txt"];

/** Start downloading the workspace code while the analysis request is in flight. */
const prefetchWorkspace = () => void import("@/features/workspace/Workspace").catch(() => undefined);

type Problem = { title: string; detail?: string } | null;

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
  const { load, dispatch } = useApp();
  const [files, setFiles] = React.useState<File[]>([]);
  const [bundle, setBundle] = React.useState<File | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [busy, setBusy] = React.useState<null | "upload" | "sample">(null);
  const [elapsed, setElapsed] = React.useState(0);
  const [problem, setProblem] = React.useState<Problem>(null);
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

  const run = async (chosen: File[], chosenBundle: File | null, origin: "upload" | "sample", name: string) => {
    const issue = checkSelection(chosen, chosenBundle, maxBytes);
    if (issue) {
      setProblem(issue);
      return;
    }
    setProblem(null);
    setBusy(origin);
    setElapsed(0);
    prefetchWorkspace();
    controller.current = new AbortController();
    try {
      const doc = await analyze({ files: chosen, bundle: chosenBundle, signal: controller.current.signal });
      history.pushState(null, "", "/review");
      load(doc, name, origin);
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
      const sample = await Promise.all(
        SAMPLE_FILES.map(async (name) => {
          const r = await fetch(`/sample/${name}`);
          if (!r.ok) throw new Error("sample missing");
          return new File([await r.blob()], name, { type: "text/plain" });
        }),
      );
      setBusy(null);
      await run(sample, null, "sample", "The Harbor Light (fictional sample)");
    } catch {
      setBusy(null);
      setProblem({ title: "The sample could not be loaded." });
    }
  };

  const onImport = async (file: File | undefined) => {
    if (!file) return;
    prefetchWorkspace();
    try {
      const check = validateDocument(JSON.parse(await file.text()));
      if (!check.ok) {
        setProblem({ title: "That file is not a PromoLens analysis.", detail: check.reason });
        return;
      }
      history.pushState(null, "", "/review");
      load(check.doc, check.doc.summary.input.name, "import");
      dispatch({ type: "notice", tone: "info", text: "Analysis opened from file. Nothing was uploaded." });
    } catch {
      setProblem({ title: "That file is not valid JSON." });
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
    <div className="min-h-dvh">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5 sm:px-8">
        <Brand />
        <span className="flex items-center gap-1.5 text-[12px] text-muted" aria-live="polite">
          <span
            className={cn(
              "size-1.5 rounded-full",
              service === "ok" ? "bg-ok" : service === "down" ? "bg-no" : "bg-faint",
            )}
          />
          {service === "ok" ? "Analysis service online" : service === "down" ? "Service unreachable" : "Checking service"}
        </span>
      </header>

      <main className="mx-auto grid max-w-6xl gap-10 px-5 pt-6 pb-16 sm:px-8 lg:grid-cols-[1fr_440px] lg:gap-16 lg:pt-14">
        <section className="max-w-xl">
          <p className="text-[12px] font-medium tracking-[0.08em] text-amber-ink uppercase">Promo discovery for microdramas</p>
          <h1 className="mt-3 font-display text-[34px] leading-[1.12] font-semibold tracking-[-0.01em] text-ink sm:text-[42px]">
            Find the clip that makes viewers press play.
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-ink-soft">
            PromoLens reads timestamped screenplays and proposes 20–60 second promo cuts with exact
            timecodes, the opening hook and the cliffhanger at the cut, so editors start from
            evidence instead of a blank timeline.
          </p>
          <dl className="mt-8 grid gap-5 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
            {[
              ["Every episode", "Deterministic candidates and a transparent baseline for the whole show."],
              ["AI review, when supplied", "Validated model judgements appear only from a real evaluation bundle."],
              ["Yours to decide", "Accept, reject and annotate cuts; export a shortlist."],
            ].map(([t, d]) => (
              <div key={t}>
                <dt className="text-[13px] font-semibold text-ink">{t}</dt>
                <dd className="mt-1 text-[13px] leading-relaxed text-muted">{d}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section aria-label="Analyze a screenplay" className="rounded-lg border border-line bg-surface p-5 shadow-card sm:p-6">
          <h2 className="font-display text-[19px] font-semibold">Analyze a screenplay</h2>
          <p className="mt-1 text-[13px] text-muted">One .zip pack or .txt episodes · up to {bytes(maxBytes)}</p>

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
              "mt-4 rounded-lg border border-dashed px-4 py-7 text-center transition-colors",
              dragging ? "border-amber bg-amber-wash" : "border-line-strong bg-paper",
            )}
          >
            <Upload className="mx-auto size-5 text-muted" aria-hidden />
            <p className="mt-2 text-[13px] text-ink-soft">
              Drop screenplay files here, or{" "}
              <button type="button" className="font-medium text-amber-ink underline-offset-4 hover:underline" onClick={() => fileInput.current?.click()}>
                choose files
              </button>
            </p>
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
            <ul className="mt-3 space-y-1" aria-label="Selected files">
              {files.slice(0, 4).map((f) => (
                <li key={f.name} className="flex items-center gap-2 text-[13px] text-ink-soft">
                  {/\.zip$/i.test(f.name) ? <FileArchive className="size-4 text-muted" /> : <FileText className="size-4 text-muted" />}
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  <span className="tabular text-[12px] text-faint">{bytes(f.size)}</span>
                </li>
              ))}
              {files.length > 4 && <li className="text-[12px] text-faint">and {files.length - 4} more</li>}
            </ul>
          )}

          <div className="mt-4 rounded-md border border-line px-3 py-2.5">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[13px] font-medium">AI evaluation bundle <span className="font-normal text-faint">(optional)</span></p>
                <p className="text-[12px] text-muted">From <code className="text-[11px]">promolens export-ai</code>. Enables AI-reviewed picks.</p>
              </div>
              {bundle ? (
                <Button size="sm" variant="ghost" onClick={() => setBundle(null)} aria-label="Remove AI bundle">
                  <X /> Remove
                </Button>
              ) : (
                <Button size="sm" onClick={() => bundleInput.current?.click()}>
                  <FileJson /> Add
                </Button>
              )}
            </div>
            {bundle && <p className="mt-1.5 truncate text-[12px] text-ink-soft">{bundle.name} · {bytes(bundle.size)}</p>}
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

          {problem && (
            <div role="alert" className="mt-4 rounded-md border border-no/25 bg-no-soft px-3 py-2.5 text-[13px] text-no">
              <p className="font-medium">{problem.title}</p>
              {problem.detail && <p className="mt-0.5 text-no/85">{problem.detail}</p>}
            </div>
          )}

          {busy ? (
            <div className="mt-4 flex items-center justify-between rounded-md bg-sunken px-3 py-2.5" aria-live="polite">
              <span className="flex items-center gap-2 text-[13px] text-ink-soft">
                <Loader2 className="size-4 animate-spin text-amber-ink" aria-hidden />
                {busy === "sample" ? "Analyzing the sample" : "Analyzing"} · <span className="tabular">{elapsed.toFixed(1)} s</span>
                {elapsed > 8 && <span className="text-faint">Large packs take a few seconds</span>}
              </span>
              <Button size="sm" variant="ghost" onClick={() => controller.current?.abort()}>
                Cancel
              </Button>
            </div>
          ) : (
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button variant="primary" size="lg" className="flex-1" onClick={onAnalyze} disabled={files.length === 0}>
                Analyze{total ? ` · ${bytes(total)}` : ""}
              </Button>
              <Button size="lg" onClick={onSample} className="flex-1 sm:flex-none">
                <Sparkles className="text-amber-ink" /> Try the fictional sample
              </Button>
            </div>
          )}

          <div className="mt-5 space-y-2 border-t border-line pt-4 text-[12px] leading-relaxed text-muted">
            <p className="flex gap-2">
              <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-faint" aria-hidden />
              Files are sent to the PromoLens analysis endpoint on its hosting platform for this
              request. The server does not intentionally keep them, and no AI model is called.
            </p>
            <p className="flex gap-2">
              <Info className="mt-0.5 size-3.5 shrink-0 text-faint" aria-hidden />
              Results are saved in this browser (IndexedDB) so a refresh keeps your work. You can
              delete them at any time.
            </p>
          </div>
          <button
            type="button"
            className="mt-3 inline-flex items-center gap-1.5 text-[12px] font-medium text-ink-soft hover:text-ink"
            onClick={() => importInput.current?.click()}
          >
            <FolderOpen className="size-3.5" /> Open a saved analysis (.json) — stays on this device
          </button>
          <input
            ref={importInput}
            type="file"
            accept=".json,application/json"
            className="sr-only"
            aria-label="Saved analysis file"
            data-testid="import-input"
            onChange={(e) => {
              void onImport(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </section>
      </main>
    </div>
  );
}
