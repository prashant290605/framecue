import {
  BadgeCheck,
  ChevronDown,
  CircleAlert,
  CircleCheck,
  Download,
  FileJson,
  FileSpreadsheet,
  FolderOpen,
  ListChecks,
  Loader2,
  MoreHorizontal,
  Pencil,
  Plus,
  Search,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import * as React from "react";
import type { ViewName } from "@/api/types";
import { Brand, BrandMark } from "@/components/Brand";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import {
  Dialog,
  DialogClose,
  DialogContent,
  Menu,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/overlays";
import {
  activeFilterCount,
  DEFAULT_FILTERS,
  DURATION_BANDS,
  type DecisionFilter,
  type DurationBand,
  type Entry,
  type Filters,
} from "@/lib/doc-index";
import { analysisJson, download, exportName, rankedCsv, shortlistCsv, shortlistJson } from "@/lib/exporters";
import { warningLabel } from "@/lib/labels";
import { validateDocument } from "@/lib/validate";
import { cn } from "@/lib/utils";
import { useApp } from "@/state/app-state";

/* Top bar ----------------------------------------------------------------------------- */

export function TopBar({ entries }: { entries: Entry[] }) {
  const { state } = useApp();
  const doc = state.doc!;
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-4 sm:gap-4 sm:px-5">
      <span className="flex items-center">
        <Brand size="sm" className="max-sm:hidden" />
        <BrandMark className="size-[22px] sm:hidden" />
      </span>
      <span className="h-6 w-px bg-line" aria-hidden />
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-[14px] leading-tight font-semibold text-ink" title={state.fileName}>
          {state.fileName}
        </h1>
        <p className="mt-0.5 hidden truncate text-[12px] leading-tight text-muted sm:block">
          {doc.summary.episodes} episodes · {doc.summary.candidates} candidates
        </p>
      </div>
      {doc.coverage.ai_evaluated > 0 && <CoverageChip />}
      <StorageChip />
      <ExportMenu entries={entries} />
      <MoreMenu />
    </header>
  );
}

/** Shown only when some clips have an AI review; without any, the list says so in one line. */
function CoverageChip() {
  const { state } = useApp();
  const cov = state.doc!.coverage;
  const sampleAi = state.origin === "sample" && cov.cache.source === "uploaded_bundle";
  const ignored = cov.cache.stale_prompt_version + cov.cache.other_profile_or_input + cov.cache.invalid_on_revalidation.length;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="hidden h-8 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-muted transition-colors hover:bg-subtle md:inline-flex"
        >
          <BadgeCheck className="size-4 text-accent" />
          AI reviewed
          <span className="font-semibold text-ink tabular">
            {cov.ai_evaluated}
            <span className="font-medium text-muted"> / {cov.total_candidates}</span>
          </span>
          <ChevronDown className="size-3.5 text-faint" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <p className="text-[14px] font-semibold text-ink">AI review coverage</p>
        <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
          {cov.complete
            ? "Every candidate has a validated AI review."
            : `${cov.ai_evaluated} of ${cov.total_candidates} candidates have a validated AI review. The rest are ranked by structure only.`}
        </p>
        {sampleAi && <SampleAiNote className="mt-2" />}
        <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-4 gap-y-1.5 border-t border-line pt-3 text-[12.5px]">
          <dt className="text-muted">Source</dt>
          <dd className="text-right font-medium text-ink">
            {cov.cache.source === "uploaded_bundle" ? (sampleAi ? "Sample bundle" : "Uploaded bundle") : "Local cache"}
          </dd>
          <dt className="text-muted">Reviews used</dt>
          <dd className="text-right font-medium text-ink tabular">{cov.cache.used} of {cov.cache.entries_scanned}</dd>
          {ignored > 0 && (
            <>
              <dt className="text-muted">Ignored (outdated or not matching)</dt>
              <dd className="text-right font-medium text-ink tabular">{ignored}</dd>
            </>
          )}
          <dt className="text-muted">Model</dt>
          <dd className="max-w-44 truncate text-right font-medium text-ink" title={cov.profile.model}>{cov.profile.model}</dd>
        </dl>
      </PopoverContent>
    </Popover>
  );
}

function StorageChip() {
  const { state } = useApp();
  const s = state.storage;
  if (s.state === "idle") return null;
  const content =
    s.state === "saving" ? (
      <><Loader2 className="size-3.5 animate-spin" /> Saving</>
    ) : s.state === "saved" ? (
      <><CircleCheck className="size-3.5 text-ok" /> Saved</>
    ) : (
      <><CircleAlert className="size-3.5 text-no" /> Not saved</>
    );
  return (
    <span
      className="hidden items-center gap-1.5 text-[12px] font-medium text-muted lg:inline-flex"
      title={s.state === "failed" ? s.message : "Saved in this browser only"}
      role="status"
    >
      {content}
    </span>
  );
}

function ExportMenu({ entries }: { entries: Entry[] }) {
  const { state } = useApp();
  const { index, view, reviews } = state;
  const doc = state.doc!;
  const decided = Object.values(reviews).filter((r) => r.decision || r.note).length;
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button size="sm" aria-label="Export">
          <Download /> <span className="hidden sm:inline">Export</span>
        </Button>
      </MenuTrigger>
      <MenuContent className="w-80">
        <MenuLabel>Ranked list</MenuLabel>
        <MenuItem
          onSelect={() =>
            download(exportName(doc, `${view === "ai" ? "ai-reviewed" : "full-show"}-ranked`, "csv"), rankedCsv(index!, view, entries, reviews), "text/csv")
          }
        >
          <FileSpreadsheet /> {view === "ai" ? "AI-reviewed" : "Full show"} · {entries.length} rows (CSV)
        </MenuItem>
        <MenuLabel>Editor shortlist</MenuLabel>
        <MenuItem disabled={!decided} onSelect={() => download(exportName(doc, "shortlist", "csv"), shortlistCsv(index!, reviews), "text/csv")}>
          <ListChecks /> Decisions and notes · {decided} (CSV)
        </MenuItem>
        <MenuItem disabled={!decided} onSelect={() => download(exportName(doc, "shortlist", "json"), shortlistJson(index!, reviews), "application/json")}>
          <ListChecks /> Decisions and notes · {decided} (JSON)
        </MenuItem>
        <MenuLabel>Full analysis</MenuLabel>
        <MenuItem onSelect={() => download(exportName(doc, "analysis", "json"), analysisJson(doc), "application/json")}>
          <FileJson /> Analysis document (JSON, re-importable)
        </MenuItem>
        <MenuSeparator />
        <p className="px-2.5 py-1.5 text-[12px] leading-snug text-muted">Exports may include confidential script text.</p>
      </MenuContent>
    </Menu>
  );
}

/** Back to the landing page (saved analyses); the open analysis stays loaded for Back. */
export function goHome() {
  history.pushState(null, "", "/");
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function MoreMenu() {
  const { state, load, remove, rename, savedKey, dispatch } = useApp();
  const [confirm, setConfirm] = React.useState(false);
  const [naming, setNaming] = React.useState(false);
  const [name, setName] = React.useState(state.fileName);
  const input = React.useRef<HTMLInputElement>(null);
  const onImport = async (file?: File) => {
    if (!file) return;
    try {
      const check = validateDocument(JSON.parse(await file.text()));
      if (!check.ok) {
        dispatch({ type: "notice", tone: "error", text: `Not a FrameCue analysis: ${check.reason}.` });
        return;
      }
      load(check.doc, check.doc.summary.input.name, "import");
      dispatch({ type: "notice", tone: "info", text: "Analysis opened from file. Nothing was uploaded." });
    } catch {
      dispatch({ type: "notice", tone: "error", text: "That file is not valid JSON." });
    }
  };
  return (
    <>
      <Menu>
        <MenuTrigger asChild>
          <Button size="icon-sm" variant="ghost" aria-label="Analysis menu" className="-mr-1.5">
            <MoreHorizontal />
          </Button>
        </MenuTrigger>
        <MenuContent>
          <MenuItem onSelect={goHome}>
            <Plus /> New analysis
          </MenuItem>
          <MenuItem
            disabled={!savedKey}
            onSelect={() => {
              setName(state.fileName);
              setNaming(true);
            }}
          >
            <Pencil /> Rename
          </MenuItem>
          <MenuItem onSelect={() => input.current?.click()}>
            <FolderOpen /> Open analysis file (.json)
          </MenuItem>
          <MenuSeparator />
          <MenuItem className="text-no data-[highlighted]:bg-no-soft [&_svg]:!text-no" onSelect={() => setConfirm(true)}>
            <Trash2 /> Delete analysis
          </MenuItem>
        </MenuContent>
      </Menu>
      <input ref={input} type="file" accept=".json,application/json" className="sr-only" aria-label="Open analysis file" onChange={(e) => { void onImport(e.target.files?.[0]); e.target.value = ""; }} />
      <Dialog open={naming} onOpenChange={setNaming}>
        <DialogContent title="Rename analysis">
          <form
            className="mt-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (savedKey && name.trim()) void rename(savedKey, name);
              setNaming(false);
            }}
          >
            <Input aria-label="Analysis name" value={name} maxLength={120} onChange={(e) => setName(e.target.value)} autoFocus />
            <div className="mt-5 flex justify-end gap-2">
              <DialogClose asChild>
                <Button type="button">Cancel</Button>
              </DialogClose>
              <Button type="submit" variant="primary" disabled={!name.trim()}>
                Save
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={confirm} onOpenChange={setConfirm}>
        <DialogContent
          title="Delete this analysis?"
          description="This removes the analysis, your decisions and notes from this browser. Exported files are not affected."
        >
          <div className="mt-6 flex justify-end gap-2">
            <DialogClose asChild>
              <Button>Cancel</Button>
            </DialogClose>
            <Button
              variant="danger"
              onClick={() => {
                setConfirm(false);
                history.pushState(null, "", "/");
                void remove();
              }}
            >
              <Trash2 /> Delete
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

/* Toolbar ----------------------------------------------------------------------------- */

export function Toolbar({ shown }: { shown: number }) {
  const { state, dispatch } = useApp();
  const { doc, view, filters, query } = state;
  const counts = { ai: doc!.views.ai.primaries, coverage: doc!.views.coverage.primaries };
  const hasAi = doc!.coverage.ai_evaluated > 0;
  return (
    <div className="space-y-2.5 border-b border-line px-4 pt-3.5 pb-3">
      {hasAi && (
      <div role="tablist" aria-label="Ranking" className="grid h-9 grid-cols-2 rounded-lg bg-subtle p-1">
        {(["ai", "coverage"] as ViewName[]).map((v) => (
          <button
            key={v}
            role="tab"
            type="button"
            aria-selected={view === v}
            onClick={() => dispatch({ type: "view", view: v })}
            className={cn(
              "flex items-center justify-center gap-2 rounded-md text-[13px] font-semibold transition-[color]",
              view === v ? "bg-surface text-ink shadow-[0_1px_2px_rgb(20_34_56/0.08)] ring-1 ring-line" : "text-muted hover:text-ink",
            )}
          >
            {v === "ai" ? "AI-reviewed" : "Full show"}
            <span className={cn("text-[11px] font-semibold tabular", view === v ? "text-accent" : "text-faint")}>{counts[v]}</span>
          </button>
        ))}
      </div>
      )}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-faint" aria-hidden />
          <Input
            type="search"
            aria-label="Search candidates"
            placeholder="Search lines, speakers, IDs"
            className="pl-9"
            value={query}
            onChange={(e) => dispatch({ type: "query", query: e.target.value })}
          />
        </div>
        <FilterPopover />
      </div>
      <ActiveFilters filters={filters} shown={shown} searching={query.length > 0} />
    </div>
  );
}

function FilterPopover() {
  const { state, dispatch } = useApp();
  const { filters, doc, index } = state;
  const set = (patch: Partial<Filters>) => dispatch({ type: "filters", filters: { ...filters, ...patch } });
  const count = activeFilterCount(filters);
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button aria-label={`Filters${count ? ` (${count} active)` : ""}`}>
          <SlidersHorizontal />
          <span className="hidden sm:inline">Filters</span>
          {count > 0 && <Badge tone="solid" className="h-[18px] min-w-[18px] justify-center rounded-full px-1">{count}</Badge>}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-3.5">
        <Field label="Episode">
          <NativeSelect
            value={filters.episode ?? ""}
            onChange={(e) => set({ episode: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">All episodes</option>
            {doc!.episodes.map((ep) => (
              <option key={ep.episode_number} value={ep.episode_number}>
                Episode {ep.episode_number} · {ep.candidate_count} candidates{ep.ai_evaluated_count ? ` · ${ep.ai_evaluated_count} AI` : ""}
              </option>
            ))}
          </NativeSelect>
        </Field>
        <Field label="Duration">
          <NativeSelect value={filters.duration} onChange={(e) => set({ duration: e.target.value as DurationBand })}>
            {Object.entries(DURATION_BANDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </NativeSelect>
        </Field>
        {doc!.coverage.ai_evaluated > 0 && (
        <>
        <Field label="AI review">
          <NativeSelect value={filters.status} onChange={(e) => set({ status: e.target.value as Filters["status"] })}>
            <option value="any">Any</option>
            <option value="ai_evaluated">AI-reviewed</option>
            <option value="not_evaluated">Not AI-reviewed</option>
            <option value="failed">AI review failed</option>
          </NativeSelect>
        </Field>
        {index!.warnings.length > 0 && (
          <Field label="AI warning">
            <NativeSelect value={filters.warning ?? ""} onChange={(e) => set({ warning: e.target.value || null })}>
              <option value="">Any or none</option>
              {index!.warnings.map((w) => <option key={w} value={w}>{warningLabel(w)}</option>)}
            </NativeSelect>
          </Field>
        )}
        </>
        )}
        <Field label="Your decision">
          <NativeSelect value={filters.decision} onChange={(e) => set({ decision: e.target.value as DecisionFilter })}>
            <option value="any">Any</option>
            <option value="undecided">Undecided</option>
            <option value="decided">Any decision</option>
            <option value="accepted">Accepted</option>
            <option value="later">Later</option>
            <option value="rejected">Rejected</option>
          </NativeSelect>
        </Field>
        <label className="flex items-center gap-2.5 text-[13px] font-medium text-ink">
          <input type="checkbox" className="size-4 accent-[var(--color-accent)]" checked={filters.alternatives} onChange={(e) => set({ alternatives: e.target.checked })} />
          Include alternative cuts
        </label>
        <div className="-mx-4 flex items-center justify-end border-t border-line px-4 pt-3">
          <Button size="sm" variant="ghost" onClick={() => dispatch({ type: "filters", filters: DEFAULT_FILTERS })} disabled={!count}>
            Clear filters
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  const id = React.useId();
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-[12px] font-semibold text-ink-soft">{label}</label>
      {React.isValidElement(children) ? React.cloneElement(children as React.ReactElement<{ id?: string }>, { id }) : children}
    </div>
  );
}

function ActiveFilters({ filters, shown, searching }: { filters: Filters; shown: number; searching: boolean }) {
  const { dispatch } = useApp();
  const chips: [string, Partial<Filters>][] = [];
  if (filters.episode !== null) chips.push([`Episode ${filters.episode}`, { episode: null }]);
  if (filters.duration !== "any") chips.push([DURATION_BANDS[filters.duration].label, { duration: "any" }]);
  if (filters.status !== "any") chips.push([filters.status === "ai_evaluated" ? "AI-reviewed" : filters.status === "failed" ? "AI review failed" : "Not AI-reviewed", { status: "any" }]);
  if (filters.warning) chips.push([warningLabel(filters.warning), { warning: null }]);
  if (filters.decision !== "any") chips.push([`Decision: ${filters.decision}`, { decision: "any" }]);
  if (filters.alternatives) chips.push(["With alternatives", { alternatives: false }]);
  if (!chips.length && !searching) return null;
  return (
    <div className="flex min-h-5 flex-wrap items-center gap-1.5 text-[12px] text-muted">
      <span className="mr-0.5 font-medium tabular" aria-live="polite">{shown} shown</span>
      {chips.map(([label, reset]) => (
        <button
          key={label}
          type="button"
          onClick={() => dispatch({ type: "filters", filters: { ...filters, ...reset } })}
          className="inline-flex h-6 items-center gap-1 rounded-md border border-line bg-surface pr-1.5 pl-2 text-[12px] font-medium text-ink-soft hover:border-line-strong hover:text-ink"
          aria-label={`Remove filter ${label}`}
        >
          {label} <X className="size-3" />
        </button>
      ))}
    </div>
  );
}

/** States plainly that the fictional sample's AI reviews are real model output. */
export function SampleAiNote({ className }: { className?: string }) {
  const { state } = useApp();
  const first = state.index ? Object.values(state.index.ai)[0] : undefined;
  const model = first?.model_reported ?? first?.model_requested ?? state.doc?.coverage.profile.model ?? "the model";
  const provider = first?.provider ?? state.doc?.coverage.profile.provider ?? "";
  return (
    <span className={cn("block text-[12.5px] leading-relaxed text-muted", className)}>
      Genuine reviews by <span className="font-semibold text-ink-soft">{model}</span>
      {provider ? ` via ${provider === "groq" ? "Groq (free tier)" : provider}` : ""}, generated offline for this fictional
      sample. The site itself never calls a model.
    </span>
  );
}
